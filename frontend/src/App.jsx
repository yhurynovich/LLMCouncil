import { useState, useEffect, useRef } from 'react';
import Sidebar from './components/Sidebar';
import ChatInterface from './components/ChatInterface';
import ModelSetSelector from './components/ModelSetSelector';
import ModelSetManager from './components/ModelSetManager';
import { api } from './api';
import './App.css';

function App() {
  const [conversations, setConversations] = useState([]);
  const [currentConversationId, setCurrentConversationId] = useState(null);
  const [currentConversation, setCurrentConversation] = useState(null);
  const [isLoading, setIsLoading] = useState(false);
  const [activeModelSet, setActiveModelSet] = useState(null);
  const [view, setView] = useState('chat'); // 'chat' or 'manage-sets'
  const [quickMode, setQuickMode] = useState(false);
  const abortRef = useRef(null);

  useEffect(() => {
    loadConversations();
  }, []);

  useEffect(() => {
    if (currentConversationId) {
      loadConversation(currentConversationId);
    }
  }, [currentConversationId]);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const loadConversation = async (id) => {
    try {
      const conv = await api.getConversation(id);
      setCurrentConversation(conv);
    } catch (error) {
      console.error('Failed to load conversation:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations([
        { id: newConv.id, created_at: newConv.created_at, title: 'New Conversation', message_count: 0 },
        ...conversations,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
  };

  const handleSelectConversation = (id) => {
    setCurrentConversationId(id);
  };

  const handleDeleteConversation = async (id) => {
    try {
      await api.deleteConversation(id);
      setConversations((prev) => prev.filter((c) => c.id !== id));
      if (currentConversationId === id) {
        setCurrentConversationId(null);
        setCurrentConversation(null);
      }
    } catch (error) {
      console.error('Failed to delete conversation:', error);
    }
  };

  const handleStop = () => {
    if (abortRef.current) {
      abortRef.current.abort();
      abortRef.current = null;
    }
    setIsLoading(false);
  };

  const handleSendMessage = async (content, files = []) => {
    if (!currentConversationId) return;
    setIsLoading(true);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const userMessage = { role: 'user', content };
      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
      }));

      const assistantMessage = {
        role: 'assistant',
        stage1: null,
        stage2: null,
        stage3: null,
        metadata: null,
        modelSetInfo: null,
        loading: { stage1: false, stage2: false, stage3: false },
      };

      setCurrentConversation((prev) => ({
        ...prev,
        messages: [...prev.messages, assistantMessage],
      }));

      await api.sendMessageStream(
        currentConversationId,
        content,
        (eventType, event) => {
          switch (eventType) {

            case 'model_set':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, modelSetInfo: event.data };
                return { ...prev, messages };
              });
              break;

            case 'stage1_start':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, loading: { ...lastMsg.loading, stage1: true } };
                return { ...prev, messages };
              });
              break;

            case 'stage1_complete':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, stage1: event.data, loading: { ...lastMsg.loading, stage1: false } };
                return { ...prev, messages };
              });
              break;

            case 'stage2_start':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, loading: { ...lastMsg.loading, stage2: true } };
                return { ...prev, messages };
              });
              break;

            case 'stage2_complete':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, stage2: event.data, metadata: event.metadata, loading: { ...lastMsg.loading, stage2: false } };
                return { ...prev, messages };
              });
              break;

            case 'stage3_start':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, loading: { ...lastMsg.loading, stage3: true } };
                return { ...prev, messages };
              });
              break;

            case 'stage3_complete':
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                messages[messages.length - 1] = { ...lastMsg, stage3: event.data, loading: { stage1: false, stage2: false, stage3: false } };
                return { ...prev, messages };
              });
              // ── Fallback: stop spinner as soon as stage3 arrives.
              // The 'complete' event may not arrive if the stream closes
              // immediately after the last data chunk (nginx/browser timing).
              setIsLoading(false);
              loadConversations();
              break;

            case 'title_complete':
              loadConversations();
              break;

            case 'complete':
              loadConversations();
              setIsLoading(false);
              break;

            case 'error':
              console.error('Stream error:', event.message);
              // Clear all spinners on error too
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                if (lastMsg?.loading) {
                  messages[messages.length - 1] = { ...lastMsg, loading: { stage1: false, stage2: false, stage3: false } };
                }
                return { ...prev, messages };
              });
              setIsLoading(false);
              break;

            default:
              console.log('Unknown event type:', eventType);
          }
        },
        activeModelSet,
        quickMode,
        controller.signal,
        files
      );
    } catch (error) {
      if (error.name === 'AbortError') {
        // User cancelled — keep partial results
      } else {
        console.error('Failed to send message:', error);
        setCurrentConversation((prev) => ({
          ...prev,
          messages: prev.messages.slice(0, -2),
        }));
      }
      setIsLoading(false);
    } finally {
      abortRef.current = null;
      setIsLoading(false);
    }
  };

  return (
    <div className="app">
      <Sidebar
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={(id) => {
          setCurrentConversationId(id);
          setView('chat');
        }}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
        modelSetSelector={
          <ModelSetSelector onSetChange={setActiveModelSet} />
        }
        onManageSets={() => setView('manage-sets')}
      />
      {view === 'chat' ? (
        <ChatInterface
          conversation={currentConversation}
          onSendMessage={handleSendMessage}
          onStop={handleStop}
          isLoading={isLoading}
          quickMode={quickMode}
          onQuickModeChange={setQuickMode}
        />
      ) : (
        <ModelSetManager onBack={() => setView('chat')} />
      )}
    </div>
  );
}

export default App;
