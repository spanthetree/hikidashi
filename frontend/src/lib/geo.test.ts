import { describe, expect, it, vi } from "vitest";

vi.mock("leaflet/dist/leaflet.css", () => ({}));
vi.mock("leaflet.markercluster/dist/MarkerCluster.css", () => ({}));

const { distance, km, readAt, writeAt } = await import("./geo");

const osaka = { lat: 34.6937, lng: 135.5023 };
const kyoto = { lat: 35.0116, lng: 135.7681 };

describe("distances", () => {
  it("measures along the globe", () => {
    expect(km(osaka, kyoto)).toBeCloseTo(43, 0);
    expect(km(osaka, osaka)).toBe(0);
  });

  it("is as precise as is useful", () => {
    expect(distance(0.347)).toEqual({ unit: "m", value: 350 });
    expect(distance(0.001)).toEqual({ unit: "m", value: 10 });
    expect(distance(4.24)).toEqual({ unit: "km", value: 4.2 });
    expect(distance(123.4)).toEqual({ unit: "km", value: 123 });
  });
});

describe("the map's position", () => {
  it("round-trips", () => {
    const at = writeAt(osaka, 12);
    expect(at).toBe("34.6937,135.5023,12");
    expect(readAt(at)).toEqual({ center: osaka, zoom: 12 });
  });

  it("wraps a longitude that went round the world", () => {
    expect(writeAt({ lat: 0, lng: 370 }, 3)).toBe("0.0000,10.0000,3");
  });

  it("refuses what is not a place", () => {
    expect(readAt("")).toBeNull();
    expect(readAt("91,0,3")).toBeNull();
    expect(readAt("a,b,c")).toBeNull();
  });
});
