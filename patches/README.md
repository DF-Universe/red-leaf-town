# 社区夏日内容的原生接入

PR #2 与 #3 的原始提交保留在 Git 历史中。运行时补丁已迁入正式模块，无需手动调用 `install_*`，也不要再安装旧版本补丁。

- 皮肤兑换和伙伴画廊：`src/red_leaf_town/application/summer_skins.py`，数据在 `data/summer_skins.json`。兑换路由沿用登录校验，注册于 `/api/red-leaf-town/summer/skins/<skin_id>/claim`；宿主将 Quart 挂载在 `/web`。
- 盛夏应援场：内容模型原生支持 `entry_fee: 0`、`one_time`、`first_clear_guide_leaves`。通关记录、战利品、2 枚引路枫叶和清空当前探索在同一次原子存档更新内完成。
- 首领大招：`src/red_leaf_town/domain/boss_moves.py`。半血当回合首发，随后每三回合一次，后续大招提前一回合预警；阶段起始回合随敌人状态持久化。
- 盛夏回响：两位顾祇可以装备；仅玩家明确点名顾祇 SP 的非全队求和检定改用敏捷，自动选人保持原规则。

皮肤图必须先上传 CDN，并核对 `data/summer_skins.json` 的 object key。兑换在夏活开放期间有效；已兑换皮肤在活动结束后仍可查看。同一伙伴的皮肤条目只追加，不重排。存档继续使用 `crossover_claims` 保存解锁和选中状态，不把皮肤阶段写进限定为 0～2 的突破字段。

部署前运行宿主环境完整测试与 `./build-front.sh`，部署后重启 `baili`。新战斗状态字段带默认值，旧存档可直接读取，无需批量迁移。

专武的全服邮件为独立运营操作，本次代码部署不会自动发送。
