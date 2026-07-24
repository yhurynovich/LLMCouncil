import { useState } from 'react';
import './Sidebar.css';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  onRenameConversation,
  modelSetSelector,
  onManageSets,
}) {
  const [deleteConfirm, setDeleteConfirm] = useState(null);
  const [editingId, setEditingId] = useState(null);
  const [editValue, setEditValue] = useState('');

  const handleDoubleClick = (e, conv) => {
    e.stopPropagation();
    setEditingId(conv.id);
    setEditValue(conv.title || '');
  };

  const handleRenameSubmit = (id) => {
    const trimmed = editValue.trim();
    if (trimmed) {
      onRenameConversation(id, trimmed);
    }
    setEditingId(null);
  };

  const handleRenameKeyDown = (e, id) => {
    if (e.key === 'Enter') {
      handleRenameSubmit(id);
    } else if (e.key === 'Escape') {
      setEditingId(null);
    }
  };

  return (
    <div className="sidebar">
      <div className="sidebar-header">
        <h1>LLM Council</h1>
        <button className="new-conversation-btn" onClick={onNewConversation}>
          + New Conversation
        </button>
      </div>

      <div className="conversation-list">
        {conversations.length === 0 ? (
          <div className="no-conversations">No conversations yet</div>
        ) : (
          conversations.map((conv) => (
            <div
              key={conv.id}
              className={`conversation-item ${
                conv.id === currentConversationId ? 'active' : ''
              }`}
              onClick={() => onSelectConversation(conv.id)}
            >
              <div className="conversation-title">
                {editingId === conv.id ? (
                  <input
                    className="conversation-title-input"
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    onBlur={() => handleRenameSubmit(conv.id)}
                    onKeyDown={(e) => handleRenameKeyDown(e, conv.id)}
                    autoFocus
                    onClick={(e) => e.stopPropagation()}
                  />
                ) : (
                  <span onDoubleClick={(e) => handleDoubleClick(e, conv)}>
                    {conv.title || 'New Conversation'}
                  </span>
                )}
              </div>
              <div className="conversation-meta">
                {conv.message_count} messages
              </div>
              <button
                className="conversation-delete-btn"
                onClick={(e) => {
                  e.stopPropagation();
                  setDeleteConfirm(conv.id);
                }}
                title="Delete conversation"
              >
                ×
              </button>
              {deleteConfirm === conv.id && (
                <div className="conversation-delete-dialog">
                  <span>Delete?</span>
                  <button
                    className="delete-confirm-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteConversation(conv.id);
                      setDeleteConfirm(null);
                    }}
                  >
                    Yes
                  </button>
                  <button
                    className="delete-cancel-btn"
                    onClick={(e) => {
                      e.stopPropagation();
                      setDeleteConfirm(null);
                    }}
                  >
                    No
                  </button>
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Model set switcher pinned to the bottom of the sidebar */}
      <div className="sidebar-footer">
        {modelSetSelector}
        <button className="manage-sets-btn" onClick={onManageSets}>
          Manage Sets
        </button>
      </div>
    </div>
  );
}
