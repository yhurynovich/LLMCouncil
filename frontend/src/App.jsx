import { useState, useEffect, useRef, useCallback } from 'react';
import AuthScreen from './components/AuthScreen';
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
  const [view, setView] = useState('chat');
  const [quickMode, setQuickMode] = useState(false);
  const [authenticated, setAuthenticated] = useState(false);
  const [authError, setAuthError] = useState('');
  const streamController = useRef(null);
  const loadConvRef = useRef(0);
  const debounceTimerRef = useRef(null);

  // Initialize auth state
  useEffect(() => {
    const checkAuth = () => {
      setAuthenticated(api.isAuthenticated());
    };
    
    checkAuth();
    
    // Listen for auth changes
    const unsubscribe = api.onAuthChange(() => {
      checkAuth();
    });
    
    return unsubscribe;
  }, []);

  useEffect(() => {
    if (!authenticated) return;
    
    loadConversations();
    // Initialize active model set from backend
    api.listModelSets().then(({ active }) => {
      setActiveModelSet(active);
    }).catch(console.error);
  }, [authenticated]);

  useEffect(() => {
    if (!currentConversationId) {
      setCurrentConversation(null);
      return;
    }
    const ctrl = new AbortController();
    (async () => {
      try {
        const conv = await api.getConversation(currentConversationId, ctrl.signal);
        if (!ctrl.signal.aborted) {
          setCurrentConversation(conv);
        }
      } catch (error) {
        if (error.name !== 'AbortError' && !ctrl.signal.aborted) {
          console.error('Failed to load conversation:', error);
        }
      }
    })();
    return () => ctrl.abort();
  }, [currentConversationId]);

  useEffect(() => {
    return () => {
      if (streamController.current) {
        streamController.current.abort();
      }
    };
  }, []);

  // Abort stream when conversation changes
  useEffect(() => {
    return () => {
      if (streamController.current) {
        streamController.current.abort();
        streamController.current = null;
      }
    };
  }, [currentConversationId]);

  // Cleanup debounce timer on unmount
  useEffect(() => {
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
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

  const handleLogin = async (username, password) => {
    setAuthError('');
    try {
      await api.login(username, password);
      setAuthenticated(true);
      setAuthError('');
      // Reload conversations after successful login
      loadConversations();
      api.listModelSets().then(({ active }) => {
        setActiveModelSet(active);
      }).catch(console.error);
    } catch (error) {
      setAuthError('Invalid username or password');
      throw error;
    }
  };

  const handleLogout = () => {
    api.logout();
    setAuthenticated(false);
    setConversations([]);
    setCurrentConversationId(null);
    setCurrentConversation(null);
  };

  // Debounced version to prevent redundant API calls
  const debouncedLoadConversations = useCallback((loadFn) => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      loadFn();
      debounceTimerRef.current = null;
    }, 300);
  }, []);

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

  // Helper to update the last assistant message safely
  const updateLastAssistantMessage = useCallback((updater) => {
    setCurrentConversation((prev) => {
      if (!prev?.messages?.length) return prev;
      const messages = [...prev.messages];
      const lastMsg = messages[messages.length - 1];
      if (lastMsg?.role !== 'assistant') return prev;
      messages[messages.length - 1] = updater(lastMsg);
      return { ...prev, messages };
    });
  }, []);

  const handleStop = () => {
    if (streamController.current) {
      streamController.current.abort();
      streamController.current = null;
    }
    setIsLoading(false);
    // Mark the last assistant message as cancelled
    setCurrentConversation((prev) => {
      if (!prev?.messages?.length) return prev;
      const messages = [...prev.messages];
      const last = messages[messages.length - 1];
      if (last?.role === 'assistant') {
        messages[messages.length - 1] = { 
          ...last, 
          error: 'Request cancelled by user', 
          loading: { stage1: false, stage2: false, stage3: false } 
        };
      }
      return { ...prev, messages };
    });
  };

  const handleSendMessage = async (content, files = []) => {
    if (!currentConversationId) return;
    setIsLoading(true);

    // Ensure we have a valid model set (default to 'free' if none selected)
    const modelSet = activeModelSet || 'free';

    // Extract file IDs from attached files
    const fileIds = files.map(f => f.file_id);

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
      error: null,
    };

    setCurrentConversation((prev) => ({
      ...prev,
      messages: [...(prev?.messages || []), assistantMessage],
    }));

    // Cancel any previous stream
    if (streamController.current) {
      streamController.current.abort();
    }
    const controller = new AbortController();
    streamController.current = controller;

    try {
      await api.sendMessageStream(
        currentConversationId,
        content,
        (eventType, event) => {
          switch (eventType) {

            case 'model_set':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                modelSetInfo: event.data,
              }));
              break;

            case 'stage1_start':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                loading: { ...lastMsg.loading, stage1: true },
              }));
              break;

            case 'stage1_complete':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                stage1: event.data,
                loading: { ...lastMsg.loading, stage1: false },
              }));
              break;

            case 'stage2_start':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                loading: { ...lastMsg.loading, stage2: true },
              }));
              break;

            case 'stage2_complete':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                stage2: event.data,
                metadata: event.metadata,
                loading: { ...lastMsg.loading, stage2: false },
              }));
              break;

            case 'stage3_start':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                loading: { ...lastMsg.loading, stage3: true },
              }));
              break;

            case 'stage3_complete':
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                stage3: event.data,
                loading: { stage1: false, stage2: false, stage3: false },
              }));
              debouncedLoadConversations(loadConversations);
              break;

            case 'title_complete':
              debouncedLoadConversations(loadConversations);
              break;

            case 'complete':
              debouncedLoadConversations(loadConversations);
              break;

            case 'error':
              console.error('Stream error:', event.message);
              updateLastAssistantMessage((lastMsg) => ({
                ...lastMsg,
                loading: { stage1: false, stage2: false, stage3: false },
              }));
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
      } else if (error.message === 'UNAUTHORIZED') {
        // Auth expired, logout
        handleLogout();
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
      if (streamController.current === controller) {
        streamController.current = null;
      }
      setIsLoading(false);
    }
  };

  // Show login screen if not authenticated
  if (!authenticated) {
    return (
      <AuthScreen 
        onLogin={handleLogin} 
        error={authError}
      />
    );
  }

  return (
    <div className="app">
      <div className="app-header">
        <h1>🤝 LLM Council</h1>
        <button className="logout-button" onClick={handleLogout}>
          Logout
        </button>
      </div>
      <Sidebar
        key={conversations.length}
        conversations={conversations}
        currentConversationId={currentConversationId}
        onSelectConversation={(id) => {
          setCurrentConversationId(id);
          setView('chat');
        }}
        onNewConversation={handleNewConversation}
        onDeleteConversation={handleDeleteConversation}
        onRenameConversation={handleRenameConversation}
        onLogout={handleLogout}
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