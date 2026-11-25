"use strict";
const assert = require("assert");
const { SLIDES, notesByPage, validateSources, renderSlide, W, H } = require("./generate_bit_opening_ppt");

const notes = notesByPage();
const captured = [];
const geometry = [];
const deck = {
  ShapeType: { rect: "rect", roundRect: "roundRect", ellipse: "ellipse", line: "line" },
  addSlide() {
    const page = { texts: [], shapes: [], note: "" };
    captured.push(page);
    return {
      addText(text, options) { page.texts.push({ text, options }); },
      addShape(type, options) { page.shapes.push({ type, options }); },
      addNotes(text) { page.note = text; }
    };
  }
};
SLIDES.forEach((spec, i) => renderSlide(deck, spec, i + 1, notes.get(i + 1), geometry));

const tests = [
  ["22 slide titles and notes match the handwritten script", () => {
    assert.strictEqual(SLIDES.length, 22);
    assert.strictEqual(notes.size, 22);
    captured.forEach((page, i) => assert.strictEqual(page.note, notes.get(i + 1).text));
  }],
  ["18 main pages and four untimed backups total 680 seconds", () => {
    assert(SLIDES.slice(0, 18).every(s => s.seconds > 0));
    assert(SLIDES.slice(18).every(s => s.seconds === 0));
    assert.strictEqual(SLIDES.reduce((sum, s) => sum + s.seconds, 0), 680);
  }],
  ["source title, seeds, folds and market-day contract remain locked", () => {
    const source = validateSources();
    assert.strictEqual(source.folds.length, 4);
    assert.strictEqual(source.folds[3].test_end, "2024-07-24");
    assert.strictEqual(source.date_only_disclosure, "next_market_session");
  }],
  ["every object stays inside the slide and body text is not shrunk", () => {
    for (const object of geometry) {
      assert(object.x >= 0 && object.y >= 0);
      assert(object.x + object.w <= W + 0.001 && object.y + object.h <= H + 0.001);
      if (object.role === "body") assert(object.fontSize >= 17);
    }
    for (const page of captured) {
      for (const item of page.texts) assert.notStrictEqual(item.options.fit, "shrink");
    }
  }],
  ["self subtraction, missing fallback and uncertainty are visible", () => {
    const graph = captured[7].texts.map(t => t.text).join("\n");
    const fusion = captured[8].texts.map(t => t.text).join("\n");
    assert(graph.includes("分子 − w(i,j)φ(s_i)") && graph.includes("分母 − w(i,j)"));
    assert(graph.includes("归一化边龄改变相对权重"));
    assert(fusion.includes("M_e = M_g = 0 → z = z_s"));
    assert(fusion.includes("不是泛化定理"));
  }],
  ["the full eleven ablations and no-live-results boundary are present", () => {
    const ablations = captured[12].texts.map(t => t.text).join("\n");
    assert(ablations.includes("5年龄仅作特征") && ablations.includes("11匿名文本"));
    const progress = captured[14].texts.map(t => t.text).join("\n");
    assert(progress.includes("main_graph_ready=false") && progress.includes("没有正式新模型分数"));
    assert(!captured.slice(0, 18).some(page => page.texts.some(t => t.text.includes("0.236579"))));
    assert(captured[18].texts.some(t => t.text.includes("不是M提升")));
  }]
];
for (const [name, run] of tests) {
  run();
  console.log("PASS " + name);
}
console.log(`All ${tests.length} presentation-source regressions passed; no licensed records used.`);
