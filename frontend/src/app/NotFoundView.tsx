import { Link } from './router';

export function NotFoundView({ path }: { path: string }) {
  return (
    <div className="notfound">
      <h1>Page not found</h1>
      <div className="path">{path}</div>
      <p>
        This address does not match any PartFlow view. Use the navigation above,
        or return to the Scan Station.
      </p>
      <Link className="btn primary" to="/scan-station">
        Go to Scan Station
      </Link>
    </div>
  );
}
