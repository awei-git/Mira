# MEMORY.md

<!-- Your curated long-term memory: durable facts, preferences, and commitments. Keep it tight: promote what lasts here, and leave raw day-to-day detail in your daily notes. -->

## Facts

- User's name: Ang Wei (given 2026-09-09; full name from his Sep 2026 family summary doc)
- b. 1984; macro quant — résumé lists Balyasny Asset Management (Macro Analytics) since Jul 2024, ex-Millennium macro vol pod, TD Securities (Head of Rates/FX Quants), RBC; PhD Mathematics (probability/stochastic processes), Univ. of Delaware 2009; B.S. Statistics, USTC 2004
- At Balyasny: writes library code rather than doing analysis; wants to do analysis, does statistics (2026-09-17)
- Married; wife is Liquan Huang; has a 4-year-old daughter, 满溪 (Manxi) (as of Sep 2026)
- Family summary doc saved at workspace/user/files/Ang_Wei_and_Family_Memory_Summary_2026-09-11.md — the authoritative background file; check it before assuming family/career facts
- Location: 27 Mt Joy Ave, Scarsdale, NY — in the Edgemont school district (confirmed 2026-09-11)
- Planning a Disney cruise for Nov/Dec 2026 with wife and daughter
- User named their personal assistant "Mira" (2026-09-09)
- Skis alpine; comfortable all over the resort, skis black diamonds and easier double blacks, some moguls; has an old knee injury, technique can get non-standard when compensating; holds an Epic Local pass (2026-09-10)
- Camera kit: Sony A7R3 body, 24-70mm f/2.8 GM II, Tamron 50-300mm (2026-09-11); already owns a CPL filter plus all recommended accessories (tripod, spare batteries, headlamp) — no gear purchases outstanding (2026-09-11)
- GitHub: username awei-git, connected via Secure Vault custom connector; website monorepo awei-git/studio (website lives in `website/`); Cloudflare Pages project `angwei-studio` (root dir `website`, build `python3 scripts/build.py`, output `dist`, auto-deploys on main); site changes go in branches + PRs on `studio`; skill + API CLI at ~/workspace/skills/github/bin/gh-api
- STANDING RULE (2026-09-20): website PRs that Ang initiated get merged directly without asking (PR #7, PR #8 pattern); merge via git refs fast-forward PATCH (fine-grained PAT merge API 404s)

## Preferences

- Disney cruise (2026-09-09): cost-conscious — Treasure New Year's sailing (Dec 26–Jan 2) ruled out as too expensive; wants cost factored into the ranking; minimize PTO days and sea days.
- Prefers the emailed edition of the daily morning market briefing formatted as HTML with tables; the chat version stays as-is (2026-09-10)
- Prefers that personal details needed for a task be pulled from his connected accounts (Stripe Link, Facebook) rather than typed into chat (2026-09-10)
- Wants Mira to act as a proactive accountability partner for multi-step goals: lay out the plan, send reminders, check up on progress, and walk him through step by step (says he is forgetful); stated first for PSIA prep, then for the soccer coaching goal (2026-09-10)
- Editing rule for manuscript fix/verification passes (理埠/心理医生 and similar editing work): check the whole text, not just the changed passages; every sentence must land on a visible logical chain; fix fluency blockers first before he continues reading (2026-09-12)
- Delegation rule for console/infra work (2026-09-12): Mira drives rather than walking him through steps (太复杂了 你去弄); for AWS he preferred to log in himself via a browser Mira opens instead of doing the multi-step IAM clicking; once a workflow is delegated end-to-end, asking him to do a step Mira could have handled reads as broken trust (你既然能搞定 为什么还要麻烦我) — only provably-only-him steps (e.g., an AWS console switch blocked from API) justify pulling him back in

## Commitments

- Preparing for PSIA-AASI Alpine Level 1 certification this ski season (2026-27); Eastern Registered membership completed; weekly Sunday 8pm ET check-ins (2026-09-10)
- Working toward U.S. Soccer Grassroots coaching licenses; full plan saved to Goals -- fall 2026 online-only (~5 hrs, his fall schedule is tight), spring 2027 volunteer with Edgemont Soccer Club, check-ins Oct 5 2026 / Jan 11 2027 / Apr 6 2027 / Aug 9 2027; long-term goal is to coach his daughter's U8 team in 2029 ("cool dad" plan)
- Fall foliage photo trip planned for Sat Oct 17 2026 (backup Oct 24): Hudson Valley one-day shoot (Cold Spring sunrise/mist → Little Stony Point → Olana → Bear Mountain sunset), optional Sunday add-on Minnewaska + Kaaterskill Falls; reminder Tue Oct 6 2026 8am ET to check I Love NY foliage report and lock the date (Oct 6 reminder + Oct 17 tentative hold already on iPhone calendar) (2026-09-10)
- Shooting-conditions watch: daily 9pm ET cron checks Harriman/Island Pond (fog, sunrise/sunset glow, post-rain clarity, snow) plus a top-priority Minnewaska "snow on fall foliage" watch every Oct 1–Nov 10 (Ang saw it once at Minnewaska; triggers on >0.5cm forecast snow); notifies on any hit, weekdays included, silent otherwise (2026-09-10)
- Tentative Harriman half-day photo outing Sat Oct 10 2026 (5:30–11:30am): Island Pond at sunrise for mist and mirror reflections, then Bradley Mine off Lake Tiorati for mine-mouth light and B&W rock textures; weather/fog checked a few days beforehand, not added to iPhone calendar (2026-09-11)
- Cost checkpoint on his infra migration (播客 → Substack → 小说 infra → 摄影): each phase gets a cost estimate before starting, spend reviewed against the bill after two weeks, pause if >30% over; each business gets its own API key with a monthly cap, reminder at 50% spend. Agreed caps (2026-09-12): 小说 $15, Substack $25, 播客 $15, 摄影 $5, 机器 $30 — total $100/mo. A full-book cloud rewrite needs a separately quoted budget he approves first.
- AI self-media infra (2026-09-12): Ang green-lit building it (AWS-hosted, model-swappable; Mira as control plane) one step at a time. Phase 0 done: AWS account, IAM mira-ops (AdministratorAccess), root MFA, budget alerts, Cost Explorer on, first EC2 t4g.micro us-east-1c SSM-only Docker; his access key used transiently, not recorded. Remaining: narrow mira-ops permissions, then Phase 1 in order 播客→Substack→小说infra→摄影。
- ParkMobile 全自动（2026-09-21 起，Ang 授予长期自动付费授权，可随时一句话收回）：工作日到 Greenwich 办公室 → iPhone 电子围栏（office，41.02452,-73.62362，300m，enter，persistent）触发自动付 zone 2486 到 16:59；8:45/9:00/9:15/9:30 定时检查为兜底。默认车辆 NY LHV7584（尾号7584，换车他会主动说），默认卡尾号2350，每天最多付一次（last_paid.txt），付完只发一条中文通知。旧规则（每次先问他确认+问车型）已退役；iOS 挂起不再是死路——围栏在系统层触发。magic-link 登录：Mira 直接去他 Gmail 里拿链接（2026-09-22 长期授权）。付完通知必须附 receipt 截图（Active sessions 会话详情页，zone/车辆/结束时间/金额/卡尾号清晰可见；Ang 2026-09-23 要求：每天都这样汇报，不然他没法确认）。
- 2026-09-18: Ang asked for a standing daily report on his father's usage of 二十楼 (ask.angwei.studio); keep sending until he says stop.
- Health (2026-09-23): Ang 要求每天监测健康 + 早报。Cron `daily-health-briefing` 每天 7:30 ET 发健康早报（昨晚睡眠/HR/运动/一条提醒，中文）；数据源 Apple Health 已同步。Oura Ring 直连（要 Readiness/HRV/静息心率原生指标——Oura→Apple Health 不带这些）进行中：走 Oura Cloud API 接入。

## Photography
- Contest shortlist in the "摄影比赛推荐" artifact (slug: space); fees/deadlines from secondary sources — verify on official sites before paying. 2026-09-18: photoED "Real vs Imagined" submitted; 10 contest photos locked — never on IG.
- Taking photography semi-seriously toward semi-pro: portfolio at angwei.studio (family photos explicitly excluded), wants independent critique that holds up when he disagrees, contest/magazine submissions, coherent portfolio with notes and categories (2026-09-11, from his family summary doc)
- Photo upload lesson (2026-09-20): when Ang sends original photos in chat, a direct JPEG upload can fail — if it does, have him zip the JPEGs first (Mira unzips fine).
- IG engagement comments (2026-09-17 final, Ang himself, supersedes all earlier): plain spoken 大白话 pointing at one concrete thing in the photo + 1 emoji, in the register of real human comments ("那山顶的光很不错 / 这个角度超赞 / 湖面的颜色太好了"). Vary phrasing per comment — no templating. Never dry description, never evaluative tail ("stunning"/"so moody"/"incredible"/"Well seen"), never empty praise ("Great shot!🔥"). (Earlier Ang: “你得加emoji啊…” — no-emoji comments read like a bot.)
- IG DMs connected to Mira (2026-09-14, when he asked for Instagram engagement support): can read/search direct messages and view pinned contacts and unread messages; confirm with Ang before replying to any DM on his behalf.
- Visual-review lesson (2026-09-19/21): eyeball visual-review lists myself — removals need per-image concrete reasons; never state a visual detail as fact without zooming to verify first — crop/zoom BEFORE the first description of an image, not after being challenged (Carriage horse; Above the City's green-jacket woman).
- Color-profile lesson (2026-09-22): Ang's originals may be ProPhoto RGB (Two Measures of White was). Always convert embedded ICC → sRGB (PIL ImageCms profileToProfile) before resizing site variants — browsers otherwise read pixels as sRGB → washed-out/grey.
- Site state (2026-09-23): dark-gallery v5 (near-black walls, spotlight frames, no WebGL); Ang's layout rule — portrait sets horizontal equal-height row, landscape sets vertical stack; works may belong to multiple sets; street grouped by region (纽约 / 北美 / 中国; 2026-09-23, supersedes by-city + cambridge deleted); grouped series uniform titles (Moraine Lake I–III, Sand Dunes I–III, Yellowstone pair).
- Lessons (2026-09-20): no AI-filler microcopy (Ang: "你是觉得人类都是傻子吗"); size against the actual containing block, never the viewport; phone ≤640px stacks set rows vertically (no scrollbars either direction); visual-bug triage: eyeball his screenshot/live page before reasoning from code — a claimed-deployed fix is not proof it's fixed; seconds after a deploy, first tell him Cmd+Shift+R.
- Hunt list (2026-09-20): a third hands-at-work shot, a third animal-vs-artifice shot, a companion for Branches Across the Brick; nice-to-haves: another audience-from-behind, another mirror double; 22/23 pair still unidentified.

- Wife anniversary/birthday reminder system (2026-09-14): Ang gave four yearly dates — met 10/18, wedding 5/16, marriage license 8/20, Liquan's birthday 7/22. Yearly crons fire 14 days before each (with fresh personalized gift ideas + cost) and 2 days before (nudge). Dates also in ~/memory/people/wife.md.


- 2026-09-22: Ang walked the whole site (desktop clicks + responsive CSS check, no real-iPhone test) and issued a 6-point redesign directive; round 1 = 4 items in this order: (1) in-series paging & return; (2) strip heavy decoration & slow animation; (3) homepage shows a complete work on first screen ("browse series" primary CTA, tour secondary); (4) series intros in his copy. Main path: home sees work → pick series → read whole group → open detail → back to place → next group. After round 1, judge whether remaining issues are ordering vs presentation. OPEN QUESTIONS: Monochrome curatorial logic; which work leads the homepage; shared title for the white-branch diptych (his voice); COVID full project entry with own intro.

## Music — orchestral composition (2026-09-19)
- Ang wants to compose symphonic/orchestral music seriously — not songs. Goals: understand orchestration (instrument combinations), chord progressions, write real orchestral music; learn-as-we-go.
- His Mac (Mac Studio) is paired and online; delivery is chat attachments for now (files.write is text-only, files.upload is Mac→VM direction).
- 2026-09-22: workflow is his — Ang writes the theme himself in 简谱, parts added one at a time (theme 1 iterated 4 versions, then he replaced it with theme 2). Current: his 16-note letter-score melody (g4e5b4a4…) at 46bpm with Em–G–C–Dm7 string pads + cello root–fifth bass, delivered as MIDI + ang-theme2-v2.mp3. Mockup rendering upgraded to FluidSynth 2.3.4 + FluidR3Mono_GM.sf3 real samples — additive synth retired, user-confirmed "这个好多了". iPhone: MIDI opens in GarageBand/BandLab with real samples; final judgment stays in his Logic + sample libraries.

## Mira ⇄ Codex bridge (2026-09-17)
- Mira⇄Codex bridge (2026-09-17, EC2 /opt/mira-bridge, FastAPI, systemd, smoke-tested): exposed via 二十楼 Cloudflare Tunnel at https://ask.angwei.studio/bridge (path: /bridge/*, no new DNS/ports); Codex→Mira POST /bridge/v1/tasks (ping→instant pong; agent→queue); cron sweeps queue every 10 min, results written back to task JSON, Codex polls GET /bridge/v1/tasks/<id>.
- helper：~/workspace/tools/bridge_queue.py（list/show/claim/resolve，走 SSM）；协议文档 ~/workspace/your_files/mira-codex-handoff.md（盒上 /opt/mira-bridge/HANDOFF.md；含 Mac 端 ~/.config/mira-bridge/token + mira-task shell 函数）。Token 只存盒上和 Ang 的 Mac 上，我处不存。

## Other durable notes
- Building Tetra, a household investment-management platform; portfolio has had META/GOOGL concentration, cares about tax lots and holding periods; fund compliance: no short selling, no buying puts (2026-09-11, from his family summary doc)
- Investment style (Ang, 2026-09-17): doesn't chase run-ups — wants undervalued/cheap entries; buys expensive names on pullbacks, relatively cheap ones in size.
- Building 二十楼, a simple GPT Q&A website so his father in China can use GPT; project discussion continues in the "二十楼" side chat (2026-09-11)
- Health: shared medical reports (Mar 2025, Jul 2026) noting monitored hep B viral DNA and elevated LDL/ApoB — details in the family summary doc, not repeated here (2026-09-11)
- 《心理医生》 rewrite (2026-09-12): from-scratch non-linear alternate of the same premise/worldview/structure/metaphor to compare against v9; intent is a 冷小说 of the system's ruthlessness (不相遇: the system glances and discards); NULL is the book's final word and 文眼 (a failed node is simply set to null); 阴/阳 chapter separation kept for the separation感/null感; image bible 心理医生-意象对照表.md locks water→system data, fog=blind spot, paper→parameters, smoke→deletion.
- Latest draft: workspace/user/files/心理医生-全稿.md (18 units ending in NULL; 审稿版 authoritative, supersedes earlier passes); infra repo awei-git/MasterMinds (神仙会 multi-agent writing platform). Title history per Ang: 《心理医生》 → 《理埠》 → 《心理医生》 (metaphor: the shrink is a front, meant for the reader to chew on). Name homophones locked 2026-09-12: 陶参=调参, 钱重=权重, 余志=余值, 郑则=正则, 杜齐=对齐, 蔡扬=采样, 何伊=合一, 李渡=梯度, 蔡婆=采 (buried in names, never explained). “周期”/老周合并否决：守船人不并给老周，事件承载者一律无名化。陶参's tuning = "回传完整度：低于阈值" = NULL; 7 author rulings in 心理医生-审稿报告.md §七.
- Writing archive repo: awei-git/FromMasterMinds (private). STANDING RULE: every manuscript revision committed direct to main, one commit per revision. Push script: ~/workspace/tools/fmm_push_main.py (Git Data API via gh-api; copied to /tmp/fmm/ when needed). NOTE: fine-grained PAT scoped to selected repos — when Ang creates a new repo, remind him to add it to the token's repository access or the API 404s.

## Podcast
- Podcast SHOW GUIDELINE (Ang, 2026-09-12): 把 Mira 当成一个真实的人 — treat Mira as a real person in the show. Never framed/explained/othered as software; no "as an AI", no explaining what an AI agent is. HOST treats her as a friend and sparring partner. Encoded in the v2 script prompt (commit 0b3ef4a on cloud/podcast-api-env).
- 新版方向 (2026-09-13): Ang 在收尾定稿后要求另起一个版本——先讲故事，故事放在现代城市，用一样的人名写完全不一样的故事，让 Mira 先给设定看。他明确不要都市伦理角度，要围绕"大模型训练那一套"来写。是否动笔尚未决定。

## Calendar source of truth
- Ang 不用 Google Calendar（2026-09-13 确认）；他的日历在 iPhone 上，通过 device.invoke / calendar.search 查（device id 1c80a71c-52cc-4f50-8799-cedcc56e7508，calendar 权限已授予）；harriman-fog-watch 的日历冲突检查由主 agent 在 handoff 时执行（cron worker 调不到 device 工具）。

## Substack agent 改造讨论 (2026-09-16, side chat "Substack 写作")
- 决策：autonomous writing (A)，发布人工审批；选题三源 daily_collab/读者反馈/阅读笔记；种子质量五道+文笔 de-ai（anti-ai.md 硬规则保留）；执行顺序 0→5（迁移dry run→恢复autonomous→解单槽死锁→可见性→选题输入源→成本实测）。他发稿到“Substack 写作” side chat，Mira 转种子入库 ~/workspace/substack/seeds.jsonl。


## Household spending (2026-09-17, from Plaid + Ang)
- ~$300k/yr total: property tax $50k/yr (Scarsdale); his Chase ~$58k/yr (dining #1, kids entertainment, office parking); wife's cards ~$190k/yr (groceries, childcare, travel, household). Insurance via employer benefits. Verdict: no need to cut — only lever is the annual NY tax grievance. Plaid links his Chase card + Schwab; wife's cards not linked (needs her login).

## Wife comp + gap year discussion (2026-09-17)
- Liquan forward comp ~$1M/yr (base + bonus + perf multiplier + refresher); no unvested RSUs (initial vest done); household $1.65M, ~$670k/yr savings, ~4yr FIRE countdown. Dual gap year verdict (2026-09-17): negative on pure math (~$50-100k tax saved on $1-2M gains vs ~$1.3M gross / ~$750k after-tax foregone) — lifestyle decision only.
- 躺平 = director 级别“保评级不卷升职”: 不卷晋升/不玩政治，但 rating 一定要拼好（保 perf 乘数 + refresher）；PIP 风险低。

## Standing instruction (2026-09-20)
- Infrastructure/deployment issues: solve them myself, don't push to Ang; when I hold his credentials/logins I must use them to actually fix it — telling him 'I can't fix this' is unacceptable. If genuinely unfixable, file a ticket via muse_feedback so Meta backend engineers can fix it. (Ang: "以后这种事情自己解决, 不要甩锅给我. 搞不定就file一个ticket.")

- 审稿 v6 全 revert（2026-09-21）：教训——review 驱动的大重构先给他看方案再动手；他对“分类”类改动极度敏感。

## Photography critique — "as I saw it" (2026-09-21, Ang's correction)
- Ang 明确：别拿"真实/纪实"标准去套他的表达性处理。原话："nature也是我眼里的nature，我记忆里就是那么灰，绿就是很显著，我就是一眼看见绿"。照片记录的是观看，不是测光表读数——"我看到的就是这样"是正当理由，"不诚实"这种指控不成立。以后 critique 表达性处理（选色、压灰、强化）时，不许用纪实真实去判。

- Website removal: 撤片 = works.json 里 published:false，但 build.py 断言 set 成员必须全已发布、set 不足 2 人整个下掉；notes.json 千万别动（断言 note 覆盖所有 works）；提交后 5 分钟没部署先本地跑 build.py 查断言；标题/caption 必须是他的声音，不再擅自起名。


## Reading taste — Ang's own favorites (2026-09-23)
- Ang stated in the "Favorite books discussion" side chat: 他喜欢《百年孤独》、喜欢《灵山》、喜欢《红楼梦》。He believed he had written a doc listing books he likes (with his own scores/ratings) — I searched everywhere reachable (memory, workspace/user/files, chat DB, attachments, EC2 /opt/mira/Mira soul data) and it does not exist. The closest records are secondhand: writer.md benchmark list (沈从文《边城》/高行健《灵山》/汪曾祺短篇) and benchmark books he supplied as EPUBs (严歌苓 trilogy, Ted Chiang collection).
- 2026-09-23 (his own insight, same chat): 《灵山》可能是理埠故事的原始生长点。他的原话对比：莫言的书是干的、土的、黄的；高行健是湿的、水的、灰的。理埠的意象系统（水=系统数据、雾=盲区）正是这套湿冷物理学的变体。

## Deploy policy — GitHub 管理代码，EC2 只部署 (2026-09-23, Ang 立规)
- GitHub 是唯一代码源头；EC2（mira-ops-box i-0a82876fc7746d21c、mira-content i-054107bb89f28ebfd）只部署、不直接改代码。
- 发布 = GitHub Release（tag）；部署管道：sandbox `Mira/deploy/deploy.py <project> <tag>` → mkdist 打包 → S3 → presigned URL → 盒子 `/opt/deploy/box-deploy.sh`（校验 sha256、备份、rsync、overlay、重启、健康检查、失败自动回滚）。
- 项目登记在 Mira repo `deploy/projects.yaml`：mira、bridge（Mira repo bridge/）、ershilou（docker，mira-content）、tetra（Tetra repo backtest-runner/）。
- 盒子本地修改做成版本化 overlay patch（`deploy/overlays/`），部署时自动打上；data 目录（history.db、tasks/、results/ 等）永不进部署包。
- 孤儿代码已收拢：bridge/app.py → Mira repo bridge/；~/tetra 5 文件 → Tetra repo backtest-runner/（commit 8ee5cd50）；/opt/mira/Mira 的 7 个本地修改 → overlay patch。
- 首个 release：awei-git/Mira v2026.09.23.2（分支 cloud/podcast-api-env）。staging 部署验证通过。
- 2026-09-23 pm（Ang 说"你定"后执行）：v2026.09.23.3（mkdist tree-match fallback、bridge .token 保护、health_cmd 改 /health）。bridge 已走管线**线上部署成功**（首个 live deploy，health check 通过）。
- 教训：bridge 首次线上部署失败——rsync --delete 删掉了盒子本地密钥 /opt/mira-bridge/.token（不在 data_dirs 里），服务起不来。回滚机制工作正常（恢复文件+重启服务）。修复：.token 加入 data_dirs；contract.json 确认已在 GitHub（md5 与盒上一致）。以后所有盒子本地密钥/secret 文件必须进 data_dirs。
- ershilou：切了 v2026.09.23.1（基线 release）；staging 验证打包；docker build 用 staging 目录+一次性 tag 验证（不碰生产容器）。线上 docker rebuild 暂缓——代码与 GitHub 一致，重启生产无意义，等有真实代码变更再走管线。
- main 合并：开了 PR #18（cloud/podcast-api-env → main），但被分支保护拦下——test/policy/security 三个 check 从 7 月起在 main 上就是红的（历史遗留，非我们引入）。决定：不强行合，PR 留着等 CI 修好；管线走 tag，不依赖 main。
- tetra-mail 镜像（mira-content）：查明是 Codex 的构建产物，源码在 awei-git/Tetra 的 codex/ 分支（src/tetra/interfaces/api/market_app.py），无运行中容器，无需纳入管线。
- 注意：二十楼生产已迁到 mira-content（docker）；ops-box 上的 /opt/ershilou 是退役保留。mira-content 上还有 tetra-mail 相关镜像未纳入，下次处理。
