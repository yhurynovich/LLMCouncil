import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import './Stage3.css';

export default function Stage3({ finalResponse }) {
  if (!finalResponse || !finalResponse.model) {
    return null;
  }

  const modelName = finalResponse.model?.includes('/')
    ? finalResponse.model.substring(finalResponse.model.indexOf('/') + 1).replace(/:free$/, '')
    : finalResponse.model;

  return (
    <div className="stage stage3">
      <h3 className="stage-title">Stage 3: Final Council Answer</h3>
      <div className="final-response">
        <div className="chairman-label">
          Chairman: {modelName || 'Unknown'}
        </div>
        <div className="final-text markdown-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>{finalResponse.response || ''}</ReactMarkdown>
        </div>
      </div>
    </div>
  );
}
