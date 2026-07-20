import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import './Stage1.css';

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);
  const [expandedError, setExpandedError] = useState(null);

  if (!responses || !Array.isArray(responses) || responses.length === 0) {
    return null;
  }

  const formatTime = (seconds) => {
    if (!seconds && seconds !== 0) return '';
    return seconds < 1 ? `${Math.round(seconds * 1000)}ms` : `${seconds}s`;
  };

  const handleErrorClick = (index) => {
    setExpandedError(expandedError === index ? null : index);
  };

  return (
    <div className="stage stage1">
      <h3 className="stage-title">Stage 1: Individual Responses</h3>

      <div className="tabs">
        {responses.map((resp, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''} ${resp.error ? 'error' : ''}`}
            onClick={() => {
              setActiveTab(index);
              if (resp.error) {
                handleErrorClick(index);
              }
            }}
          >
            {resp.model ? (resp.model.split('/')[1] || resp.model) : 'Unknown'}
            {resp.error && <span className="error-indicator">!</span>}
            {!resp.error && resp.response_time != null && (
              <span className="tab-time">{formatTime(resp.response_time)}</span>
            )}
          </button>
        ))}
      </div>

      <div className="tab-content">
        {responses[activeTab].error ? (
          <div className="error-content">
            <div className="model-name error">
              {responses[activeTab].model || 'Unknown'}
              <span className="error-badge">Failed</span>
            </div>
            <div className="error-details">
              <div className="error-message">
                {responses[activeTab].error}
              </div>
              {expandedError === activeTab && (
                <div className="error-expandable">
                  <p>This model did not return a response. Possible reasons:</p>
                  <ul>
                    <li>API key invalid or missing</li>
                    <li>Model unavailable or rate limited</li>
                    <li>Network timeout</li>
                    <li>Invalid request format</li>
                  </ul>
                </div>
              )}
              <button
                className="error-toggle"
                onClick={() => handleErrorClick(activeTab)}
              >
                {expandedError === activeTab ? 'Hide Details' : 'Show Details'}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="model-name">
              {responses[activeTab].model || 'Unknown'}
              {responses[activeTab].response_time != null && (
                <span className="response-time-badge">
                  {formatTime(responses[activeTab].response_time)}
                </span>
              )}
            </div>
            <div className="response-text markdown-content">
              <ReactMarkdown>{responses[activeTab].response || ''}</ReactMarkdown>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
