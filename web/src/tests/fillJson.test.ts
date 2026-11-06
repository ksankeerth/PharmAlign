import { describe, expect, it } from "vitest";
import { itemsOf, labelOf, parseJson, pickFields } from "../lib/fillJson";

const fields = { name: "string", count: "number", active: "boolean", languages: "string[]" } as const;

describe("reading a file", () => {
  it("parses JSON and gives a short error for bad JSON", () => {
    expect(parseJson('{"a": 1}')).toEqual({ ok: true, value: { a: 1 } });
    const bad = parseJson("{not json");
    expect(bad.ok).toBe(false);
    if (!bad.ok) expect(bad.error.length).toBeLessThan(140);
    expect(parseJson("").ok).toBe(false);
  });

  it("gives the items of a bare list, {items}, {key} or one object on its own", () => {
    expect(itemsOf([{ name: "A" }, { name: "B" }])).toHaveLength(2);
    expect(itemsOf({ items: [{ name: "A" }, { name: "B" }] })).toHaveLength(2);
    expect(itemsOf({ people: [{ name: "A" }] }, "people")).toEqual([{ name: "A" }]);
    expect(itemsOf({ name: "A" })).toEqual([{ name: "A" }]);
  });

  it("ignores anything that is not an object, and wrappers without a list", () => {
    expect(itemsOf([1, "x", null, [2], { name: "A" }])).toEqual([{ name: "A" }]);
    expect(itemsOf(null)).toEqual([]);
    expect(itemsOf("text")).toEqual([]);
    expect(itemsOf({ items: "nope" })).toEqual([]);
    expect(itemsOf({ people: {} }, "people")).toEqual([]);
  });

  it("names an item by its first non-empty label, else the fallback", () => {
    expect(labelOf({ display_name: "Ada", name: "ada" }, "Item 1")).toBe("Ada");
    expect(labelOf({ display_name: "  ", name: "Bo" }, "Item 1")).toBe("Bo");
    expect(labelOf({ title: "T", label: "L" }, "Item 1")).toBe("T");
    expect(labelOf({ label: "L", id: "x" }, "Item 1")).toBe("L");
    expect(labelOf({ id: "p-1" }, "Item 1")).toBe("p-1");
    expect(labelOf({ name: 3 }, "Item 2")).toBe("Item 2");
  });
});

describe("picking the form's fields", () => {
  it("keeps a whole object that fits", () => {
    const whole = { name: "Bahrain", count: 3, active: true, languages: ["en", "ar"] };
    expect(pickFields(whole, fields)).toEqual(whole);
  });

  it("drops unknown keys", () => {
    expect(pickFields({ name: "Bahrain", id: "ws-bh" }, fields)).toEqual({ name: "Bahrain" });
  });

  it("drops values of the wrong type", () => {
    const out = pickFields({ name: 5, count: "3", active: "yes", languages: ["en", 2] }, fields);
    expect(out).toEqual({});
    expect(pickFields({ count: Number.NaN, languages: "en" }, fields)).toEqual({});
  });

  it("returns nothing for a value that is not an object", () => {
    expect(pickFields(null, fields)).toEqual({});
    expect(pickFields([{ name: "A" }], fields)).toEqual({});
  });
});
