interface Props {
  view: string;
}

export default function PlaceholderPage({ view }: Props) {
  return (
    <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <p className="ui" style={{ color: 'var(--text-muted)', fontSize: 14 }}>
        {view} — coming in the next phase
      </p>
    </div>
  );
}
