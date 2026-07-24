import { render, screen } from '@testing-library/react';
import { afterEach, expect, test, vi } from 'vitest';

import { App } from './App';

afterEach(() => {
    vi.unstubAllGlobals();
});

function stubFetch(implementation: () => Promise<Response>) {
    vi.stubGlobal('fetch', vi.fn(implementation));
}

test('shows the loading state while the health request is pending', () => {
    // A fetch that never settles keeps the component in its initial state.
    stubFetch(() => new Promise<Response>(() => {}));

    render(<App />);

    expect(
        screen.getByRole('heading', { name: 'PartFlow' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent(
        'Checking backend connection…',
    );
});

test('shows the connected state when the health endpoint succeeds', async () => {
    stubFetch(() =>
        Promise.resolve(
            new Response(
                JSON.stringify({
                    status: 'ok',
                    service: 'partflow-api',
                    database: 'connected',
                }),
                {
                    status: 200,
                    headers: { 'Content-Type': 'application/json' },
                },
            ),
        ),
    );

    render(<App />);

    expect(await screen.findByRole('status')).toHaveTextContent(
        'Backend connected.',
    );
});

test('shows the unavailable state when the health request fails', async () => {
    stubFetch(() => Promise.reject(new TypeError('Failed to fetch')));

    render(<App />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
        'Backend unavailable.',
    );
});

test('shows the unavailable state when the backend returns a non-success response', async () => {
    stubFetch(() =>
        Promise.resolve(
            new Response(JSON.stringify({ status: 'unavailable' }), {
                status: 503,
            }),
        ),
    );

    render(<App />);

    expect(await screen.findByRole('alert')).toHaveTextContent(
        'Backend unavailable.',
    );
});
