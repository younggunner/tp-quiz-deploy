# tp-quiz-deploy

TinyPumper quiz funnel — GitHub Pages. Serves `quiz.tinypumper.com` (after DNS cutover; see
`Mission_Control/docs/Infrastructure/Hosting/quiz-instapage-exit-plan.md`).

- `/quiz` — Typeform uVP2g9QP (embed.js + data-tf-hidden) + attribution script ported VERBATIM from Instapage. The relay parses this embed mechanism differently from GB's iframe — never unify.
- `/approved` — booking page (dual Calendly, transparent pricing). Fresh copy 2026-07-13: fake scarcity removed, Cometly + secondary pixel 769334093165499 stripped (GTM-5PQGM83 carries all tags).
- `/success` — post-booking confirmation (Calendly redirect target).
- NO CNAME file yet — added at DNS cutover so github.io preview URLs work first.
