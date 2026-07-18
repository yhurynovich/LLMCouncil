/**
 * ModelSetSelector — pill-style switcher shown in the Sidebar.
 * Fetches available sets from the backend and lets the user switch between them.
 */
import { useState, useEffect } from 'react';
import { api } from '../api';
import './ModelSetSelector.css';

export default function ModelSetSelector({ onSetChange }) {
  const [sets, setSets]       = useState({});
  const [active, setActive]   = useState(null);
  const [tooltip, setTooltip] = useState(null);
  const [saving, setSaving]   = useState(false);

  useEffect(() => {
    api.listModelSets()
      .then(({ sets, active }) => {
        setSets(sets);
        setActive(active);
      })
      .catch(console.error);
  }, []);

  const handleSelect = async (setId) => {
    if (setId === active || saving) return;
    setSaving(true);
    try {
      await api.setActiveModelSet(setId);
      setActive(setId);
      onSetChange && onSetChange(setId);
    } catch (e) {
      console.error('Failed to switch model set:', e);
    } finally {
      setSaving(false);
    }
  };

  if (!active) return null;

  return (
    <div className="model-set-selector">
      <div className="model-set-label">Council</div>
      <div className="model-set-pills">
        {Object.entries(sets).map(([id, set]) => (
          <button
            key={id}
            className={`model-set-pill ${active === id ? 'active' : ''} ${saving ? 'saving' : ''}`}
            onClick={() => handleSelect(id)}
            onMouseEnter={() => setTooltip(id)}
            onMouseLeave={() => setTooltip(null)}
            title={set.description}
          >
            <span className="pill-icon">{set.icon}</span>
            <span className="pill-label">{set.label}</span>
            {active === id && <span className="pill-active-dot" />}
          </button>
        ))}
      </div>

      {tooltip && sets[tooltip] && (
        <div className="model-set-tooltip">
          <div className="tooltip-desc">{sets[tooltip].description}</div>
          <div className="tooltip-models">
            {sets[tooltip].council.map(m => (
              <div key={m} className="tooltip-model">
                {m.split('/')[1]?.replace(/:free$/, '') ?? m}
              </div>
            ))}
          </div>
          <div className="tooltip-chairman">
            👑 {sets[tooltip].chairman.split('/')[1]?.replace(/:free$/, '') ?? sets[tooltip].chairman}
          </div>
          {active === tooltip && (
            <div className="tooltip-default">✓ Default (persists across sessions)</div>
          )}
        </div>
      )}
    </div>
  );
}
