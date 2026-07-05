export default function SyncBar({
  metaSyncing,
  googleSyncing,
  onMetaSync,
  onGoogleSync,
  metaLastSynced,
  googleLastSynced,
  metaError,
  googleError,
  metaPushNote,
  googleSyncNote,
}) {
  return (
    <div className="sync-bar">
      <div className="sync-bar-buttons">
        <div className="sync-bar-platform">
          <button className="btn-primary" onClick={onMetaSync} disabled={metaSyncing}>
            {metaSyncing ? "Syncing Meta…" : "Sync Meta"}
          </button>
          {metaLastSynced && (
            <span className="sync-timestamp">Last: {metaLastSynced}</span>
          )}
        </div>
        <div className="sync-bar-platform">
          <button className="btn-primary" onClick={onGoogleSync} disabled={googleSyncing}>
            {googleSyncing ? "Syncing Google…" : "Sync Google"}
          </button>
          {googleLastSynced && (
            <span className="sync-timestamp">Last: {googleLastSynced}</span>
          )}
        </div>
      </div>

      {metaError && <p className="sync-bar-error">Meta: {metaError}</p>}
      {googleError && <p className="sync-bar-error">Google: {googleError}</p>}
      {metaPushNote && (
        <p className={`sync-note sync-note-${metaPushNote.type}`}>{metaPushNote.text}</p>
      )}
      {googleSyncNote && (
        <p className={`sync-note sync-note-${googleSyncNote.type}`}>{googleSyncNote.text}</p>
      )}
    </div>
  );
}
