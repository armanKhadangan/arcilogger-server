# Katkı rehberi / Contributing

## Ground rules (from the build specification)

1. Raw logs are evidence: never modify, reorder, re-encode or delete raw data outside the
   retention job.
2. No secrets in the repository, no telemetry, no external CDNs or fonts; everything is bundled.
3. Never claim "legally compliant". Use "5651 yükümlülüklerini desteklemek için tasarlanmıştır".
4. Do not commit third-party binaries (Zamane jar, Java, installers) — they are downloaded and
   checksum-verified during setup.
5. Do not invent facts about Kamu SM, the law or device formats: make the behaviour configurable
   and write a test that fails loudly if the assumption is wrong.

## Branches and commits

- Work on a feature branch, open a pull request, keep `main` green.
- [Conventional Commits](https://www.conventionalcommits.org/): `feat(core): …`, `fix(web): …`,
  `docs: …`, `test(ingest): …`, `chore(ci): …`. Scopes: `core`, `collector`, `ingest`, `api`,
  `worker`, `web`, `deploy`, `docs`, `ci`.

## Code

| Area | Rules |
|---|---|
| Python | 3.12, typed everywhere, `mypy --strict`, ruff (lint + format, line length 100), no naive datetimes (ruff `DTZ`), `pathlib` only, binary file I/O for log data |
| TypeScript | strict mode, ESLint (typescript-eslint strict), Prettier, no `any` except the documented TanStack column type |
| UI | Colours only through tokens in `web/src/styles/globals.css`; every string in `web/src/locales/{tr,en}.json` (the developer UI kit's in `web/src/locales/ui-kit/`, not in the shipped bundle); status never by colour alone (icon + text); WCAG 2.1 AA |
| Docs | Turkish first, English second; every module has a docstring explaining its job |

## Checks before pushing

```bash
uv run poe check                 # Python: ruff, mypy, pytest
cd web && npm run check          # web: eslint, prettier, tsc, vitest, build
npm run e2e                      # web: Playwright + axe (builds its own bundle first)
```

Optional: `uv tool install pre-commit && pre-commit install` runs the fast checks on every commit.

## Line endings

`.gitattributes` forces LF (CRLF only for `.ps1`/`.cmd`). Log fixtures are byte-exact and pinned by
`tests/fixtures/format/SHA256SUMS`; a Windows Git client that converts them makes the tests fail.

## Ekip çalışma düzeni / Team workflow

**Türkçe**

1. **Erişim (bir kez).** Depo sahibi GitHub hesabınızı depoya *collaborator* olarak ekler. Siz de
   claude.ai hesabınızla <https://claude.ai/connect-github> adresinden GitHub'ı bağlarsınız.
2. **Ortam.** Ayrıca bir şey yapmanız gerekmez: ajan her oturumun başında `tools/dev/cloud-setup.sh`
   betiğini kendisi çalıştırır (`CLAUDE.md`). Betik, testlerin istediği PostgreSQL 16'yı ve test
   kullanıcısını kurar.
3. **Göreviniz.** GitHub *Issues* altında size atanmış issue bir iş paketidir: başlangıç dalı,
   okunacak sözleşme bölümleri (`design/`), sizin dosyalarınız, dokunulmayacak dosyalar, "bitti"
   listesi ve komutlar oradadır.
4. **Başlatma.** Yeni bir Claude Code oturumu açıp şunu yazın:
   `GitHub issue #<N>'yi uygula. Önce CLAUDE.md'yi ve issue'yu baştan sona oku. origin/wave-3'ten başla, iş bitince wave-3'e pull request aç.`
5. **Pull request.** CI kendiliğinden çalışır. Yönetici (proje sahibinin Claude oturumu) her PR'ı
   inceler. Yorum gelince ajanınıza `PR #<M>'deki inceleme yorumlarını ele al` deyin. Onaylanan
   PR'ı yönetici birleştirir; `wave-3` ya da `main` dalına kimse doğrudan göndermez.
6. **Kurallar.** Yalnızca kendi issue'nuzdaki dosyaları değiştirin. Başka bir paketin dosyasında
   değişiklik gerekiyorsa bunu PR açıklamasına ve issue'ya yazın. Sözleşmeden sapma ve açık
   sorular da PR açıklamasına yazılır.
7. **Soru.** Issue'ya yorum yazın; yönetici yanıtlar.

**English**

1. **Access (once).** The repository owner adds your GitHub account as a collaborator. You connect
   GitHub to your claude.ai account at <https://claude.ai/connect-github>.
2. **Environment.** Nothing to do: the agent runs `tools/dev/cloud-setup.sh` itself at the start of
   every session (`CLAUDE.md`). It installs the PostgreSQL 16 and the test role the suites need.
3. **Your task.** The GitHub issue assigned to you is one work package: it names the branch to
   start from, the contract sections to read (`design/`), the files you own, the files not to
   touch, the done-when list and the commands.
4. **Starting.** Open a new Claude Code session and write:
   `Implement GitHub issue #<N>. First read CLAUDE.md and the whole issue. Start from origin/wave-3 and open a pull request into wave-3 when done.`
5. **Pull request.** CI runs by itself. The manager (the owner's Claude session) reviews every pull
   request. When comments arrive, tell your agent `Address the review comments on PR #<M>`. The
   manager merges approved pull requests; nobody pushes to `wave-3` or `main` directly.
6. **Rules.** Change only the files your issue gives you. If another package's file needs a change,
   write it in the pull request description and on the issue. Deviations from the contract and
   open questions go into the pull request description too.
7. **Questions.** Comment on the issue; the manager answers.
