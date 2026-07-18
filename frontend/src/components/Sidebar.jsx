import { useState } from 'react';
import './Sidebar.css';

export default function Sidebar({
  conversations,
  currentConversationId,
  onSelectConversation,
  onNewConversation,
  onDeleteConversation,
  modelSetSelector,
  onManageSets,
}) {
  const [deleteConfirm, setDeleteConfirm] = useState(null);

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
                {conv.title || 'New Conversation'}
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
