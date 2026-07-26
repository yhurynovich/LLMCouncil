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
  const loadConvRef = useRef(0);

  useEffect(() => {
    loadConversations();
  }, []);

  useEffect(() => {
    if (!currentConversationId) {
      setCurrentConversation(null);
      return;
    }
    const reqId = ++loadConvRef.current;
    let cancelled = false;
    (async () => {
      try {
        const conv = await api.getConversation(currentConversationId);
        if (!cancelled && reqId === loadConvRef.current) {
          setCurrentConversation(conv);
        }
      } catch (error) {
        if (!cancelled && error.name !== 'AbortError' && reqId === loadConvRef.current) {
          console.error('Failed to load conversation:', error);
        }
      }
    })();
    return () => { cancelled = true; };
  }, [currentConversationId]);

  useEffect(() => {
    return () => {
      if (abortRef.current) {
        abortRef.current.abort();
        abortRef.current = null;
      }
    };
  }, []);

  const loadConversations = async () => {
    try {
      const convs = await api.listConversations();
      setConversations(convs);
    } catch (error) {
      console.error('Failed to load conversations:', error);
    }
  };

  const handleNewConversation = async () => {
    try {
      const newConv = await api.createConversation();
      setConversations((prev) => [
        { id: newConv.id, created_at: newConv.created_at, title: 'New Conversation', message_count: 0 },
        ...prev,
      ]);
      setCurrentConversationId(newConv.id);
    } catch (error) {
      console.error('Failed to create conversation:', error);
    }
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

  const handleRenameConversation = async (id, title) => {
    try {
      await api.renameConversation(id, title);
      setConversations((prev) =>
        prev.map((c) => (c.id === id ? { ...c, title } : c))
      );
      if (currentConversationId === id) {
        setCurrentConversation((prev) => (prev ? { ...prev, title } : prev));
      }
    } catch (error) {
      console.error('Failed to rename conversation:', error);
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
        messages: [...(prev?.messages || []), userMessage],
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
        messages: [...(prev?.messages || []), assistantMessage],
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
              loadConversations();
              break;

            case 'title_complete':
              loadConversations();
              break;

            case 'complete':
              loadConversations();
              break;

            case 'error':
              console.error('Stream error:', event.message);
              setCurrentConversation((prev) => {
                const messages = [...prev.messages];
                const lastMsg = messages[messages.length - 1];
                if (lastMsg?.loading) {
                  messages[messages.length - 1] = { ...lastMsg, loading: { stage1: false, stage2: false, stage3: false } };
                }
                return { ...prev, messages };
              });
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
        setCurrentConversation((prev) => {
          const messages = [...prev.messages];
          const last = messages[messages.length - 1];
          if (last?.role === 'assistant') {
            messages[messages.length - 1] = {
              ...last,
              error: error.message || 'Failed to generate response',
              loading: { stage1: false, stage2: false, stage3: false },
            };
          }
          return { ...prev, messages };
        });
      }
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
        onRenameConversation={handleRenameConversation}
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
