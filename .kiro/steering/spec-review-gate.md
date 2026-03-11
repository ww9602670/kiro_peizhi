# Specs 审核门禁

## Specs 路径
- `.kiro/specs/{feature-name}/`（kebab-case）
- 每个 spec 包含：`requirements.md`、`design.md`、`tasks.md`

## 审核门禁规则
**硬规则**：任何 specs 变更必须经过 gpt-5.2（codex-gpt52）审核，获得 PASS 后才能进入实施阶段。

### 必审项（FAIL 阻塞）
- requirements.md：需求完整性、可测试性、无歧义
- design.md：技术方案可行性、与现有架构一致性
- tasks.md：拆分合理性 + 依赖正确性 + 验收标准（DoD）

### 建议项（non-blocking）
- tasks.md 估时合理性

### 审核记录（推荐）
```markdown
## Review
| 日期 | 版本 | 结论 | 关键 P0 |
|------|------|------|---------|
| 2026-02-28 | v1 | PASS | 无 |
```
