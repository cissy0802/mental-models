# mental-models

本仓运行约定：

## 新页面必带共享脚本（免触发 inject-comments 机器人提交）

生成任何 `*.html`（含 `.en.html`）时，在 `</body>` 前直接写入这 4 行，勿遗漏：

```html
<script src="https://cissy0802.github.io/comments.js" defer></script>
<script src="https://cissy0802.github.io/search.js" defer></script>
<script src="https://cissy0802.github.io/index-button.js" defer></script>
<script src="https://cissy0802.github.io/i18n-tts.js" defer></script>
```

这样 CI 的 inject-comments 不会再对新页面追加自动提交。
