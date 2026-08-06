import { Component } from 'react';
import type { ErrorInfo, ReactNode } from 'react';

import { ErrorState } from '../components/view-states';

interface ViewErrorBoundaryProps {
  /** Current route path — logged so a report names the exact URL. */
  route: string;
  /** Resolved view key (e.g. 'production-board') — logged with the error. */
  viewKey: string;
  children: ReactNode;
}

interface ViewErrorBoundaryState {
  hasError: boolean;
}

/**
 * View-level error boundary around the lazy-loaded views. A runtime
 * error inside one view (or a failed lazy chunk load) must not blank
 * the whole application: the shell — navigation, offline banner,
 * theme — stays interactive and the failed view renders the standard
 * ErrorState instead. The original error is logged with its route and
 * view key for diagnosis; the UI never shows a raw stack trace.
 *
 * Navigating to another route resets the boundary in place
 * (componentDidUpdate below) — deliberately WITHOUT a remount key on
 * the children: the Scan Station mode toggle and the Production Board
 * kiosk toggle are route changes over one continuously mounted view
 * whose state (active page, station context) must survive the switch.
 * The boundary reports failures — it never masks them: the underlying
 * error stays in the console exactly as thrown.
 */
export class ViewErrorBoundary extends Component<
  ViewErrorBoundaryProps,
  ViewErrorBoundaryState
> {
  state: ViewErrorBoundaryState = { hasError: false };

  static getDerivedStateFromError(): ViewErrorBoundaryState {
    return { hasError: true };
  }

  componentDidCatch(error: unknown, info: ErrorInfo) {
    // One structured line with full context; the raw error object keeps
    // its stack for the browser console — only the UI omits it.
    console.error(
      `[PartFlow] View "${this.props.viewKey}" crashed at route "${this.props.route}"`,
      { route: this.props.route, viewKey: this.props.viewKey, error, info },
    );
  }

  componentDidUpdate(prevProps: ViewErrorBoundaryProps) {
    // Leaving the crashed route clears the error state so the next
    // view renders normally; staying on the route keeps the ErrorState
    // until the user retries explicitly.
    if (this.state.hasError && prevProps.route !== this.props.route) {
      this.setState({ hasError: false });
    }
  }

  private retry = () => {
    this.setState({ hasError: false });
  };

  render() {
    if (this.state.hasError) {
      return (
        <ErrorState
          message="This view ran into an error and could not be displayed."
          detail="The rest of the application is still available. Retry the view, or use the navigation to continue — details were logged to the browser console."
          onRetry={this.retry}
        />
      );
    }
    return this.props.children;
  }
}
