/**
 * ModelSetManager — full CRUD page for managing model sets.
 * Create, edit, delete model sets and pick models from the available list.
 */
import { useState, useEffect, useMemo } from 'react';
import { api } from '../api';
import './ModelSetManager.css';

const BUILTIN_IDS = ['free', 'smart', 'reasonable', 'privacy'];

export default function ModelSetManager({ onBack }) {
  const [sets, setSets] = useState({});
  const [active, setActive] = useState(null);
  const [availableModels, setAvailableModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState(null); // null = list view, "new" = create, or set_id = edit
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [providerFilter, setProviderFilter] = useState('');
  const [providers, setProviders] = useState({});
  const [deleteConfirm, setDeleteConfirm] = useState(null);

  // Form state
  const [form, setForm] = useState({
    set_id: '',
    label: '',
    icon: '',
    description: '',
    council: [],
    chairman: '',
  });

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setLoading(true);
    try {
      const [setsData, modelsData, providersData] = await Promise.all([
        api.listModelSets(),
        api.listAvailableModels(),
        api.listProviders(),
      ]);
      setSets(setsData.sets);
      setActive(setsData.active);
      setAvailableModels(modelsData.models);
      setProviders(providersData.providers);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  };

  const filteredModels = useMemo(() => {
    let models = availableModels;
    if (providerFilter) {
      models = models.filter((m) => m.provider === providerFilter);
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      models = models.filter(
        (m) =>
          m.id.toLowerCase().includes(q) ||
          m.name.toLowerCase().includes(q)
      );
    }
    return models;
  }, [availableModels, searchQuery, providerFilter]);

  const startCreate = () => {
    setForm({
      set_id: '',
      label: '',
      icon: '',
      description: '',
      council: [],
      chairman: '',
    });
    setEditing('new');
    setError(null);
  };

  const startEdit = (setId) => {
    const ms = sets[setId];
    setForm({
      set_id: setId,
      label: ms.label,
      icon: ms.icon,
      description: ms.description,
      council: [...ms.council],
      chairman: ms.chairman,
    });
    setEditing(setId);
    setError(null);
  };

  const handleSave = async () => {
    setSaving(true);
    setError(null);
    try {
      if (editing === 'new') {
        if (!form.set_id.trim()) {
          setError('Set ID is required');
          setSaving(false);
          return;
        }
        if (!form.label.trim()) {
          setError('Label is required');
          setSaving(false);
          return;
        }
        await api.createModelSet({
          set_id: form.set_id,
          label: form.label,
          icon: form.icon,
          description: form.description,
          council: form.council,
          chairman: form.chairman,
        });
      } else {
        await api.updateModelSet(editing, {
          label: form.label,
          icon: form.icon,
          description: form.description,
          council: form.council,
          chairman: form.chairman,
        });
      }
      setEditing(null);
      await loadData();
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (setId) => {
    try {
      await api.deleteModelSet(setId);
      setDeleteConfirm(null);
      if (active === setId) setActive('free');
      await loadData();
    } catch (e) {
      setError(e.message);
    }
  };

  const toggleModel = (modelId) => {
    setForm((prev) => {
      // Handle model sets: add all models from the set
      if (modelId.startsWith('set/')) {
        const setId = modelId.replace('set/', '');
        const setModels = sets[setId]?.council || [];
        const allIncluded = setModels.every(m => prev.council.includes(m));
        if (allIncluded) {
          // Remove all models from this set
          return { ...prev, council: prev.council.filter(m => !setModels.includes(m)) };
        } else {
          // Add all models from this set
          const newCouncil = [...new Set([...prev.council, ...setModels])];
          return { ...prev, council: newCouncil };
        }
      }
      // Handle individual models
      const council = prev.council.includes(modelId)
        ? prev.council.filter((m) => m !== modelId)
        : [...prev.council, modelId];
      return { ...prev, council };
    });
  };

  const formatPrice = (pricing) => {
    if (!pricing || !pricing.prompt) return '';
    const prompt = parseFloat(pricing.prompt);
    const completion = parseFloat(pricing.completion);
    if (prompt === 0 && completion === 0) return 'Free';
    return `$${(prompt * 1e6).toFixed(2)}/$${(completion * 1e6).toFixed(2)} per 1M`;
  };

  if (loading) {
    return (
      <div className="msm-page">
        <div className="msm-loading">Loading model sets...</div>
      </div>
    );
  }

  // ── List View ──────────────────────────────────────────────────────────────
  if (!editing) {
    return (
      <div className="msm-page">
        <div className="msm-header">
          <button className="msm-back-btn" onClick={onBack}>
            &larr; Back
          </button>
          <h1>Model Sets</h1>
          <button className="msm-create-btn" onClick={startCreate}>
            + New Set
          </button>
        </div>

        {error && <div className="msm-error">{error}</div>}

        <div className="msm-sets-grid">
          {Object.entries(sets).map(([id, ms]) => (
            <div key={id} className={`msm-set-card ${active === id ? 'active' : ''}`}>
              <div className="msm-card-header">
                <span className="msm-card-icon">{ms.icon}</span>
                <div className="msm-card-title-area">
                  <h3>{ms.label}</h3>
                  <span className="msm-card-id">{id}</span>
                </div>
                {active === id && <span className="msm-active-badge">ACTIVE</span>}
                {BUILTIN_IDS.includes(id) && <span className="msm-builtin-badge">Built-in</span>}
              </div>
              <p className="msm-card-desc">{ms.description}</p>
              <div className="msm-card-models">
                <div className="msm-card-section">
                  <span className="msm-card-section-label">Council ({ms.council.length})</span>
                  <div className="msm-card-model-list">
                    {ms.council.map((m) => (
                      <span key={m} className="msm-card-model-tag">
                        {m.split('/')[1]?.replace(/:free$/, '') ?? m}
                      </span>
                    ))}
                  </div>
                </div>
                <div className="msm-card-section">
                  <span className="msm-card-section-label">Chairman</span>
                  <span className="msm-card-chairman">
                    {ms.chairman.split('/')[1]?.replace(/:free$/, '') ?? ms.chairman}
                  </span>
                </div>
              </div>
              <div className="msm-card-actions">
                <button className="msm-edit-btn" onClick={() => startEdit(id)}>
                  Edit
                </button>
                {!BUILTIN_IDS.includes(id) && (
                  <button
                    className="msm-delete-btn"
                    onClick={() => setDeleteConfirm(id)}
                  >
                    Delete
                  </button>
                )}
              </div>

              {deleteConfirm === id && (
                <div className="msm-delete-dialog">
                  <p>Delete "{ms.label}"?</p>
                  <div className="msm-delete-actions">
                    <button onClick={() => handleDelete(id)} className="msm-delete-confirm">
                      Yes, delete
                    </button>
                    <button onClick={() => setDeleteConfirm(null)} className="msm-delete-cancel">
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    );
  }

  // ── Edit / Create View ────────────────────────────────────────────────────
  return (
    <div className="msm-page">
      <div className="msm-header">
        <button className="msm-back-btn" onClick={() => setEditing(null)}>
          &larr; Back
        </button>
        <h1>{editing === 'new' ? 'New Model Set' : `Edit: ${form.label}`}</h1>
      </div>

      {error && <div className="msm-error">{error}</div>}

      <div className="msm-form">
        <div className="msm-form-row">
          <div className="msm-field">
            <label>Set ID</label>
            <input
              type="text"
              value={form.set_id}
              onChange={(e) => setForm({ ...form, set_id: e.target.value })}
              disabled={editing !== 'new'}
              placeholder="e.g. my-custom-set"
              className="msm-input"
            />
          </div>
          <div className="msm-field">
            <label>Label</label>
            <input
              type="text"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              placeholder="e.g. My Custom Set"
              className="msm-input"
            />
          </div>
          <div className="msm-field msm-field-small">
            <label>Icon</label>
            <input
              type="text"
              value={form.icon}
              onChange={(e) => setForm({ ...form, icon: e.target.value })}
              placeholder="e.g. MY"
              className="msm-input"
              maxLength={6}
            />
          </div>
        </div>

        <div className="msm-field">
          <label>Description</label>
          <input
            type="text"
            value={form.description}
            onChange={(e) => setForm({ ...form, description: e.target.value })}
            placeholder="Short description of this model set"
            className="msm-input"
          />
        </div>

        <div className="msm-model-picker">
          <div className="msm-picker-header">
            <h3>Select Council Models ({form.council.length} selected)</h3>
            <div className="msm-picker-filters">
              <select
                value={providerFilter}
                onChange={(e) => setProviderFilter(e.target.value)}
                className="msm-provider-filter"
              >
                <option value="">All Providers</option>
                {Object.keys(providers).map((p) => (
                  <option key={p} value={p}>{p}</option>
                ))}
              </select>
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder="Search models..."
                className="msm-search"
              />
            </div>
          </div>

          <div className="msm-model-list">
            {filteredModels.length === 0 && (
              <div className="msm-no-models">No models found</div>
            )}
            {filteredModels.map((m) => {
              const isSet = m.id.startsWith('set/');
              let isSelected;
              if (isSet) {
                const setId = m.id.replace('set/', '');
                const setModels = sets[setId]?.council || [];
                isSelected = setModels.length > 0 && setModels.every(model => form.council.includes(model));
              } else {
                isSelected = form.council.includes(m.id);
              }
              return (
                <div
                  key={m.id}
                  className={`msm-model-item ${isSelected ? 'selected' : ''} ${isSet ? 'model-set-item' : ''}`}
                  onClick={() => toggleModel(m.id)}
                >
                  <div className="msm-model-check">
                    {isSelected ? '✓' : ''}
                  </div>
                  <div className="msm-model-info">
                    <div className="msm-model-name">{m.name}</div>
                    <div className="msm-model-id">{m.id}</div>
                  </div>
                  <div className="msm-model-meta">
                    <span className={`msm-provider-badge provider-${m.provider}`}>
                      {isSet ? 'set' : m.provider}
                    </span>
                    {formatPrice(m.pricing) && (
                      <span className="msm-model-price">{formatPrice(m.pricing)}</span>
                    )}
                    {m.context_length && (
                      <span className="msm-model-ctx">
                        {(m.context_length / 1000).toFixed(0)}K ctx
                      </span>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="msm-field">
          <label>Chairman Model</label>
          <select
            value={form.chairman}
            onChange={(e) => setForm({ ...form, chairman: e.target.value })}
            className="msm-select"
          >
            <option value="">-- Select chairman --</option>
            {availableModels.map((m) => (
              <option key={m.id} value={m.id}>
                {m.name} ({m.provider})
              </option>
            ))}
          </select>
        </div>

        <div className="msm-form-actions">
          <button className="msm-save-btn" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : editing === 'new' ? 'Create Set' : 'Save Changes'}
          </button>
          <button className="msm-cancel-btn" onClick={() => setEditing(null)}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
