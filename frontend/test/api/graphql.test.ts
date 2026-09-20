import { afterEach, describe, expect, test, vi } from "vitest";

import { GraphQLRequestError, requestGraphQL } from "../../src/api/graphql.ts";

function respond(body: unknown, status = 200): ReturnType<typeof vi.fn> {
  const fetch = vi.fn(() => Promise.resolve(Response.json(body, { status })));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("requestGraphQL", () => {
  test("posts the document and variables as JSON and returns the data", async () => {
    const fetch = respond({ data: { dataVersion: 7 } });
    const controller = new AbortController();
    const result = await requestGraphQL<{ dataVersion: number }>(
      "query Version { dataVersion }",
      { unused: 1 },
      controller.signal,
    );
    expect(result).toEqual({ dataVersion: 7 });
    expect(fetch).toHaveBeenCalledWith("/graphql", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ query: "query Version { dataVersion }", variables: { unused: 1 } }),
      signal: controller.signal,
    });
  });

  test("reports every GraphQL error message", async () => {
    respond({ data: undefined, errors: [{ message: "first" }, { message: "second" }] });
    const failure = requestGraphQL("query Broken { dataVersion }", {}, new AbortController().signal);
    await expect(failure).rejects.toThrow(GraphQLRequestError);
    await expect(failure).rejects.toMatchObject({
      messages: ["first", "second"],
      message: "first; second",
    });
  });

  test("reports HTTP failures such as an unavailable API", async () => {
    respond({ detail: "Bad Gateway" }, 502);
    await expect(
      requestGraphQL("query Version { dataVersion }", {}, new AbortController().signal),
    ).rejects.toThrow(
      "The API responded with HTTP 502",
    );
  });
});
