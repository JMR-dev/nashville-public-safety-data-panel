// GraphQL over HTTP POST, without a client library: TanStack Query owns caching.

export class GraphQLRequestError extends Error {
  readonly messages: string[];

  constructor(messages: string[]) {
    super(messages.join("; "));
    this.name = "GraphQLRequestError";
    this.messages = messages;
  }
}

interface GraphQLResponse<T> {
  data?: T;
  errors?: { message: string }[];
}

export async function requestGraphQL<T>(
  query: string,
  variables: Record<string, unknown>,
  signal: AbortSignal,
): Promise<T> {
  const response = await fetch("/graphql", {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({ query, variables }),
    signal,
  });
  if (!response.ok) {
    throw new Error(`The API responded with HTTP ${String(response.status)}`);
  }
  const body = (await response.json()) as GraphQLResponse<T>;
  if (body.errors) {
    throw new GraphQLRequestError(body.errors.map((error) => error.message));
  }
  return body.data as T;
}
