import { Link } from '../../app/link';
import { PageNote } from '../../components/PageNote';
import { EmptyState } from '../../components/view-states';

/**
 * The production `/management/work-orders/completed` route (GUI_DESIGN
 * §11.5) while the completion workflow does not exist yet: a Work
 * Order completes when every demand line is fully allocated, and
 * allocation arrives with a later phase (Phase 10). Until then no Work
 * Order can complete, so there is no history to show — this page says
 * exactly that instead of simulating one. The route itself stays real
 * and deep-linkable, and the back action returns to the active list.
 */
export function CompletedWorkOrdersUnavailable() {
  return (
    <section className="wo-view" aria-label="Completed Work Orders">
      <Link to="/management/work-orders" className="cwo-back">
        ‹ Work Orders
      </Link>
      <div className="wo-head">
        <h1>Completed Work Orders</h1>
      </div>
      <p className="wo-sub">
        The permanent, read-only history of completed Work Orders — a Work Order
        completes when every demand line has been fully allocated.
      </p>
      <EmptyState message="No Work Order can be completed yet — completion is derived from allocation, which is not part of this release." />
      <PageNote>
        Every active Work Order stays on the{' '}
        <Link to="/management/work-orders">Work Orders</Link> list. When the
        allocation workflow ships, completed Work Orders leave the active list
        and appear here permanently — nothing will be deleted.
      </PageNote>
    </section>
  );
}
