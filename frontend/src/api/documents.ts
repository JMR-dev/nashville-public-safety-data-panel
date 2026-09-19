// The operations the dashboard sends. They live as .graphql files so the API's own tests can
// run these exact documents against the schema and its limits.

export { default as ANCHOR } from "./operations/anchor.graphql?raw";
export { default as CALL_DETAIL } from "./operations/call-detail.graphql?raw";
export { default as FEED } from "./operations/feed.graphql?raw";
export { default as FILTER_VALUES } from "./operations/filter-values.graphql?raw";
export { default as MAP_CALLS } from "./operations/map-calls.graphql?raw";
export { default as NEW_CALLS } from "./operations/new-calls.graphql?raw";
export { default as STATUS } from "./operations/status.graphql?raw";
export { default as SUMMARY } from "./operations/summary.graphql?raw";
