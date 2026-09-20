import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

import type { OpenVersionSource } from "./api/events.ts";
import { Monitor } from "./components/monitor.tsx";

// Data changes arrive as /events notifications, so nothing is refetched merely for being old.
export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { staleTime: Infinity, refetchOnWindowFocus: false },
    },
  });
}

interface AppProperties {
  openEvents: OpenVersionSource;
  client?: QueryClient;
}

export function App({ openEvents, client }: AppProperties) {
  const [queryClient] = useState(() => client ?? createQueryClient());
  return (
    <QueryClientProvider client={queryClient}>
      <Monitor openEvents={openEvents} />
    </QueryClientProvider>
  );
}
