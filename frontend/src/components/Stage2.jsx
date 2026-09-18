import { useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import rehypeSanitize from 'rehype-sanitize';
import './Stage2.css';

const shortModelName = (model) => {
  if (!model) return 'Unknown';
  const idx = model.indexOf('/');
  return idx >= 0 ? model.substring(idx + 1).replace(/:free$/, '') : model;
};

function deAnonymizeText(text, labelToModel) {
  if (!labelToModel || !text) return text;

  const sortedLabels = Object.keys(labelToModel).sort((a, b) => b.length - a.length);
  let result = text;
  for (const label of sortedLabels) {
    const model = labelToModel[label];
    const modelShortName = shortModelName(model).replace(/[*_~`[\]\\]/g, '');
    const escapedLabel = label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    // Escape $ characters in the replacement string to prevent regex injection
    const safeReplacement = `**${modelShortName.replace(/\$/g, '$$$$')}**`;
    result = result.replace(new RegExp(escapedLabel, 'g'), safeReplacement);
  }
  return result;
}

export default function Stage2({ rankings, labelToModel, aggregateRankings }) {
  const [activeTab, setActiveTab] = useState(0);

  if (!rankings || !Array.isArray(rankings) || rankings.length === 0) {
    return null;
  }

  const safeIndex = Math.min(activeTab, rankings.length - 1);

  return (
    <div className="stage stage2">
      <h3 className="stage-title">Stage 2: Peer Rankings</h3>

      <h4>Raw Evaluations</h4>
      <p className="stage-description">
        Each model evaluated all responses (anonymized as Response A, B, C, etc.) and provided rankings.
        Below, model names are shown in <strong>bold</strong> for readability, but the original evaluation used anonymous labels.
      </p>

      <div className="tabs">
        {rankings.map((rank, index) => (
          <button
            key={index}
            className={`tab ${activeTab === index ? 'active' : ''}`}
            onClick={() => setActiveTab(index)}
          >
            {shortModelName(rank.model)}
          </button>
        ))}
      </div>

      <div className="tab-content">
        <div className="ranking-model">
          {rankings[safeIndex].model || 'Unknown'}
        </div>
        <div className="ranking-content markdown-content">
          <ReactMarkdown remarkPlugins={[remarkGfm]} rehypePlugins={[rehypeSanitize]}>
            {deAnonymizeText(rankings[safeIndex]?.ranking || '', labelToModel)}
          </ReactMarkdown>
        </div>

        {rankings[safeIndex]?.parsed_ranking &&
         rankings[safeIndex]?.parsed_ranking.length > 0 && (
          <div className="parsed-ranking">
            <strong>Extracted Ranking:</strong>
            <ol>
              {rankings[activeTab]?.parsed_ranking.map((label, i) => (
                <li key={i}>
                  {labelToModel?.[label]
                    ? shortModelName(labelToModel[label])
                    : label}
                </li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {aggregateRankings && aggregateRankings.length > 0 && (
        <div className="aggregate-rankings">
          <h4>Aggregate Rankings (Street Cred)</h4>
          <p className="stage-description">
            Combined results across all peer evaluations (lower score is better):
          </p>
          <div className="aggregate-list">
            {aggregateRankings.map((agg, index) => (
              <div key={index} className="aggregate-item">
                <span className="rank-position">#{index + 1}</span>
                <span className="rank-model">
                  {shortModelName(agg.model)}
                </span>
                <span className="rank-score">
                  Avg: {agg.average_rank.toFixed(2)}
                </span>
                <span className="rank-count">
                  ({agg.rankings_count} votes)
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
