import { buildSchema, parse, validate } from "graphql";
import { expect, test } from "vitest";

import schemaText from "../../../docs/schema.graphql?raw";
import * as documents from "../../src/api/documents.ts";

const schema = buildSchema(schemaText);

test.each(Object.entries(documents))(
  "%s is valid against the committed API schema",
  (_name, document) => {
    expect(validate(schema, parse(document))).toEqual([]);
  },
);
