# mental-models

本仓运行约定：

## 新页面必带共享脚本（免触发 inject-comments 机器人提交）

生成任何 `*.html`（含 `.en.html`）时，在 `</body>` 前直接写入这 4 行，勿遗漏：

```html
<script src="https://hub.cissychen.com/comments.js" defer></script>
<script src="https://hub.cissychen.com/search.js" defer></script>
<script src="https://hub.cissychen.com/index-button.js" defer></script>
<script src="https://hub.cissychen.com/i18n-tts.js" defer></script>
```

这样 CI 的 inject-comments 不会再对新页面追加自动提交。

## 十条要点（2026-10-17 起，新页必写）

本站每期本身就是四个跨领域思维模型，**不加「越界」框**；只加这一节。中文页 `*-dayNN.html` 和英文页 `*-dayNN.en.html` 都要写。

- **位置**：最后一张模型卡片（`</section>`）之后、`<footer class="footer">` 之前，做成一张独立卡片。
- **正好 10 条**，`<ol class="takeaways">`。读者直接拿它记笔记：每条一句完整的话，约 20–45 字，**单独抄出去也能看懂**——写出模型名和概念名，不用「它」「这个模型」这类要靠上下文的代词。
- 覆盖四个模型最值得记的定义、机制、例子和误用边界，大致每个模型 2–3 条；顺序跟正文走。提出者的人名不必每条都当主语开头：全组里出现两三次就够，其余各条直接写观点或机制（写「系统的行为由结构决定，而不是由个体意图决定」，不写「梅多斯认为……」）。
- **只总结正文已经写过的东西**，不加新信息、不放引语、不抄提示词框（prompt-box）。
- 中文页标题 `十条要点`，英文页标题 `Ten takeaways`。

```html
<section class="card tk">
<h2>十条要点</h2>
<ol class="takeaways">
<li>…</li>
</ol>
</section>
```

CSS（现有页面 `<style>` 里没有这段，照抄旧页样式后**追加进去**；中英两页都要）：

```css
.card.tk { border-left-color: #0a6fb8; }
.takeaways { counter-reset: tk; list-style: none; margin: 14px 0 0; padding: 0; border: 1px solid rgba(10,111,184,0.22); border-radius: 8px; background: #f5f9fd; }
.takeaways li { counter-increment: tk; position: relative; margin: 0; padding: 10px 16px 10px 50px; border-top: 1px solid rgba(10,111,184,0.12); font-size: 15px; line-height: 1.75; color: #2d3436; }
.takeaways li:first-child { border-top: none; }
.takeaways li::before { content: counter(tk, decimal-leading-zero); position: absolute; left: 16px; top: 11px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12.5px; font-weight: 600; color: #0a6fb8; }
```

篇幅：十条要点约 350 字，`.maxchars` 为它从 3500 上调到 3850（2026-10-17）；四张模型卡片的篇幅不变。`publish.sh` 会卡新页正好 10 条。
