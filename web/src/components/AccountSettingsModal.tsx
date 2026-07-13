import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { zoteroApi } from '../api/zotero';
import styles from './AccountSettingsModal.module.css';

export default function AccountSettingsModal({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const [apiKey, setApiKey] = useState('');
  const [saved, setSaved] = useState(false);

  const { data: status } = useQuery({
    queryKey: ['zotero-status'],
    queryFn: () => zoteroApi.getStatus(),
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
        </div>

        <div className={styles.footer}>
          <button className={styles.closeBtn} onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  );
}
