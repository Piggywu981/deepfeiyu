# 插件目录

每个插件一个子目录，里面放 `plugin.json`：

```text
plugins/
  my-plugin/
    plugin.json
    （可选）plugin.py
```

最小声明式 HTTP 插件：

```json
{
  "name": "my-plugin",
  "description": "说明",
  "auto_reply": false,
  "tools": [
    {
      "name": "my_tool",
      "description": "工具说明",
      "parameters": {"type": "object", "properties": {}},
      "method": "GET",
      "url": "https://example.com/api?q={q}"
    }
  ]
}
```

工具名会加 `plugin__<插件名>__<工具名>` 前缀。`auto_reply` 默认 false：插件只在控制台可用。
需要 Python 插件时，加 `"trusted": true` 和 `"entry": "plugin.py"`，并在 `plugin.py` 里实现
`register(add_tool)`。Python 插件与应用同权限运行，只装可信代码。

也支持放到 `data/plugins/`（运行数据目录，不随仓库提交）。
