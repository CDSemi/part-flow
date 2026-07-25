/**
 * Production-safe placeholder for a view whose workflow has no real data
 * source yet. Production builds exclude the development-only mock views
 * (see dev-views.ts), so every route renders this explicit state until
 * its backend slice exists.
 */
export function UnconnectedView({ title }: { title: string }) {
  return (
    <div className="unconnected" role="status">
      <h1>{title}</h1>
      <p>
        This workflow is not connected to a production data source yet. The view
        arrives with its backend slice in a later implementation phase.
      </p>
    </div>
  );
}
