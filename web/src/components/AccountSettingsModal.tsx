import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { zoteroApi } from '../api/zotero';
import { llmCredentialsApi } from '../api/llmCredentials';
import styles from './AccountSettingsModal.module.css';

export default function AccountSettingsModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState('');
  const [saved, setSaved] = useState(false);

  const [llmApiKey, setLlmApiKey] = useState('');
  const [llmSaved, setLlmSaved] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['zotero-status'],
    queryFn: () => zoteroApi.getStatus(),
  });

  const { data: llmStatus } = useQuery({
    queryKey: ['llm-status'],
    queryFn: () => llmCredentialsApi.getStatus(),
  });

  const saveMutation = useMutation({
    mutationFn: () => zoteroApi.saveCredentials(apiKey.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['zotero-status'] });
      setApiKey('');
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    },
  });

  const disconnectMutation = useMutation({
    mutationFn: () => zoteroApi.disconnect(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['zotero-status'] }),
  });

  const llmSaveMutation = useMutation({
    mutationFn: () => llmCredentialsApi.saveCredentials(llmApiKey.trim()),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['llm-status'] });
      setLlmApiKey('');
      setLlmSaved(true);
      setTimeout(() => setLlmSaved(false), 2000);
    },
  });

  const llmDisconnectMutation = useMutation({
    mutationFn: () => llmCredentialsApi.disconnect(),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['llm-status'] }),
  });

  return (
    <div className={styles.overlay} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className={styles.modal}>
        <div className={styles.header}>
          <div className={styles.title}>Account settings</div>
        </div>

        <div className={styles.body}>
          <div className={styles.statusRow}>
            <div
              className={`${styles.statusDot} ${status?.connected ? styles.statusDotConnected : ''}`}
            />
            {status?.connected ? (
              <>
                <span>Connected to Zotero</span>
                <span className={styles.statusUserId}>{status.zotero_user_id}</span>
              </>
            ) : (
              <span>Zotero not connected</span>
            )}
            {status?.connected && (
              <button
                className={styles.disconnectBtn}
                onClick={() => disconnectMutation.mutate()}
                disabled={disconnectMutation.isPending}
              >
                Disconnect
              </button>
            )}
          </div>

          <p className={styles.helpText}>
            Connect your personal Zotero library to search and import papers directly. Generate
            an API key at{' '}
            <a href="https://www.zotero.org/settings/keys" target="_blank" rel="noreferrer">
              zotero.org/settings/keys
            </a>{' '}
            — we look up your account from the key itself, so that's the only thing you need to
            paste in.
          </p>

          <div className={styles.field}>
            <label className={styles.label}>API key</label>
            <input
              className={styles.input}
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={status?.connected ? '••••••••••••••••' : 'paste your API key'}
            />
          </div>

          <div className={styles.saveRow}>
            <button
              className={styles.saveBtn}
              disabled={!apiKey.trim() || saveMutation.isPending}
              onClick={() => saveMutation.mutate()}
            >
              {saveMutation.isPending ? 'Connecting…' : 'Save & connect'}
            </button>
            {saved && <span className={styles.savedHint}>Connected</span>}
          </div>

          {saveMutation.error && (
            <div className={styles.errorMsg}>
              {(saveMutation.error as { response?: { data?: { detail?: string } } })?.response
                ?.data?.detail ?? 'Could not connect to Zotero.'}
            </div>
          )}

          <div className={styles.sectionDivider} />

          <div className={styles.statusRow}>
            <div
              className={`${styles.statusDot} ${llmStatus?.connected ? styles.statusDotConnected : ''}`}
            />
            {llmStatus?.connected ? (
              <span>Connected to Claude</span>
            ) : (
              <span>Claude not connected</span>
            )}
            {llmStatus?.connected && (
              <button
                className={styles.disconnectBtn}
                onClick={() => llmDisconnectMutation.mutate()}
                disabled={llmDisconnectMutation.isPending}
              >
                Disconnect
              </button>
            )}
          </div>

          <p className={styles.helpText}>
            Connect your own Claude API key so Find/summarize requests run against your own
            Anthropic account instead of the shared server key. Generate a key at{' '}
            <a href="https://console.anthropic.com/settings/keys" target="_blank" rel="noreferrer">
              console.anthropic.com/settings/keys
            </a>.
          </p>

          <div className={styles.field}>
            <label className={styles.label}>API key</label>
            <input
              className={styles.input}
              type="password"
              value={llmApiKey}
              onChange={(e) => setLlmApiKey(e.target.value)}
              placeholder={llmStatus?.connected ? '••••••••••••••••' : 'paste your API key'}
            />
          </div>

          <div className={styles.saveRow}>
            <button
              className={styles.saveBtn}
              disabled={!llmApiKey.trim() || llmSaveMutation.isPending}
              onClick={() => llmSaveMutation.mutate()}
            >
              {llmSaveMutation.isPending ? 'Connecting…' : 'Save & connect'}
            </button>
            {llmSaved && <span className={styles.savedHint}>Connected</span>}
          </div>

          {llmSaveMutation.error && (
            <div className={styles.errorMsg}>
              {(llmSaveMutation.error as { response?: { data?: { detail?: string } } })?.response
                ?.data?.detail ?? 'Could not connect to Claude.'}
            </div>
          )}
        </div>

        <div className={styles.footer}>
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
