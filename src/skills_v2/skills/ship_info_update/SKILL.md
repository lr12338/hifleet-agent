# 船舶信息更新 V2

仅使用事务工具 `prepare_ship_update`、`commit_ship_update` 和 `cancel_ship_update`。
不得暴露或调用底层写入 API。先准备规范化草稿，向用户展示字段与校验错误，仅在
同会话内获得明确确认后才提交。只有当写入适配器返回确切的 `success` 状态时才能说
"更新成功"；accepted、pending、dry-run、unknown 和 failed 状态必须保守描述。
