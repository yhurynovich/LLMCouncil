import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import './Stage1.css';

const hasError = (resp) => resp.error && String(resp.error).trim() !== '';

export default function Stage1({ responses }) {
  const [activeTab, setActiveTab] = useState(0);
  const [expandedError, setExpandedError] = useState(null);

  if (!responses || !Array.isArray(responses) || responses.length === 0) {
    return null;
  }

  const safeIndex = Math.min(activeTab, responses.length - 1);

  const formatTime = (seconds) => {
    const num = Number(seconds);
    if (Number.isNaN(num)) return '';
    return num < 1 ? `${Math.round(num * 1000)}ms` : `${num}s`;
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
            className={`tab ${activeTab === index ? 'active' : ''} ${hasError(resp) ? 'error' : ''}`}
            onClick={() => {
              setActiveTab(index);
              if (hasError(resp)) {
                handleErrorClick(index);
              } else {
                setExpandedError(null);
              }
            }}
          >
            {resp.model ? (resp.model.split('/')[1] || resp.model) : 'Unknown'}
            {hasError(resp) && <span className="error-indicator">!</span>}
            {!hasError(resp) && resp.response_time != null && (
              <span className="tab-time">{formatTime(resp.response_time)}</span>
            )}
          </button>
        ))}
      </div>

      <div className="tab-content">
        {hasError(responses[safeIndex]) ? (
          <div className="error-content">
            <div className="model-name error">
              {responses[safeIndex]?.model || 'Unknown'}
              <span className="error-badge">Failed</span>
            </div>
            <div className="error-details">
              <div className="error-message">
                {responses[safeIndex]?.error}
              </div>
              {expandedError === safeIndex && (
                <div className="error-expandable">
                  <p>Full error message:</p>
                  <pre className="error-trace">{responses[safeIndex]?.error}</pre>
                </div>
              )}
              <button
                className="error-toggle"
                onClick={() => handleErrorClick(safeIndex)}
              >
                {expandedError === safeIndex ? 'Hide Details' : 'Show Details'}
              </button>
            </div>
          </div>
        ) : (
          <>
            <div className="model-name">
              {responses[safeIndex]?.model || 'Unknown'}
              {responses[safeIndex]?.response_time != null && (
                <span className="response-time-badge">
                  {formatTime(responses[safeIndex]?.response_time)}
                </span>
              )}
            </div>
            <div className="response-text markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{responses[safeIndex]?.response || ''}</ReactMarkdown>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
