import { vi } from "vitest";

type Variables = Record<string, unknown>;
type Handler = (variables: Variables) => unknown;

export interface Request {
  operation: string;
  variables: Variables;
}

// Answers GraphQL requests by operation name, the way the real API would over HTTP.
// A handler may return data, throw to produce a GraphQL error, or return a Promise.
export class FakeApi {
  private readonly handlers = new Map<string, Handler>();
  readonly requests: Request[] = [];

  constructor() {
    vi.stubGlobal("fetch", (_url: string, init: RequestInit) => this.respond(init));
  }

  private async respond(init: RequestInit): Promise<Response> {
    const { query, variables } = JSON.parse(init.body as string) as {
      query: string;
      variables: Variables;
    };
    const operation = /query (\w+)/.exec(query)?.[1] ?? "anonymous";
    this.requests.push({ operation, variables });
    const handler = this.handlers.get(operation);
    if (!handler) {
      return Response.json({ errors: [{ message: `No fake for ${operation}` }] });
    }
    try {
      return Response.json({ data: await handler(variables) });
    } catch (error) {
      return Response.json({ errors: [{ message: (error as Error).message }] });
    }
  }

  on(operation: string, handler: Handler): this {
    this.handlers.set(operation, handler);
    return this;
  }

  calls(operation: string): Variables[] {
    return this.requests
      .filter((request) => request.operation === operation)
      .map((request) => request.variables);
  }
}
