import { describe, expect, it } from "vitest";
import { pickBestVoice } from "../../static/v2/js/app/core.js";

function voice(name, lang) {
  return { name, lang };
}

describe("pickBestVoice", () => {
  it("returns null when there are no voices", () => {
    expect(pickBestVoice([])).toBeNull();
  });

  it("prefers an English British male voice over other English/non-English voices", () => {
    const voices = [
      voice("Microsoft Zira - English (United States)", "en-US"),
      voice("Microsoft George - English (United Kingdom)", "en-GB"),
      voice("Google français", "fr-FR"),
    ];
    expect(pickBestVoice(voices).name).toBe(
      "Microsoft George - English (United Kingdom)",
    );
  });

  it("keeps the first voice when every voice scores the same (stable sort)", () => {
    const voices = [
      voice("Google 中文", "zh-CN"),
      voice("Google Español", "es-ES"),
    ];
    expect(pickBestVoice(voices)).toBe(voices[0]);
  });

  it("breaks ties between equally-scored voices by original order", () => {
    const voices = [
      voice("Microsoft David - English (US)", "en-US"),
      voice("Microsoft James - English (US)", "en-US"),
    ];
    expect(pickBestVoice(voices)).toBe(voices[0]);
  });
});
