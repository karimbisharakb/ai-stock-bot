# V1 Release Checklist — Personal Investing System

**Purpose:** Steps to verify the system is production-ready before daily real-money use.  
**Railway URL:** `https://ai-stock-bot-production.up.railway.app`

---

## 1. Railway Environment Variables to Verify

Log into Railway → Project → Variables and confirm:

| Variable | Required Value | Safe Default |
|----------|---------------|--------------|
| `ANTHROPIC_API_KEY` | Set to Anthropic API key | — |
| `TWILIO_ACCOUNT_SID` | Set to Twilio SID | — |
| `TWILIO_AUTH_TOKEN` | Set to Twilio auth token | — |
| `TWILIO_PHONE_NUMBER` | `+14155238886` | — |
| `MY_PHONE_NUMBER` | Your WhatsApp number (+1...) | — |
| `API_SECRET` | Set to a strong random string | — |
| `NEWS_API_KEY` | Set (or leave unset for no news) | unset = skip |
| `LEGACY_NOTIFICATIONS_ENABLED` | **Must be unset or `false`** | false |
| `UNIFIED_NOTIFICATIONS_ENABLED` | Unset or `false` | false |
| `ALPHA_SHADOW_ENABLED` | Unset or `false` | false |
| `ALPHA_ALERTS_ENABLED` | **Must be unset or `false`** | false |
| `ALPHA_NOTIFICATIONS_ENABLED` | **Must be unset or `false`** | false |
| `ALPHA_NOTIFICATIONS_DRY_RUN_ONLY` | Unset or `true` | true |
| `EOD_BRIEF_ENABLED` | `true` only if you want EOD alerts | false |
| `WEEKLY_REVIEW_ENABLED` | `true` only if you want Friday summaries | false |
| `NOTIFICATION_CENTER_ENABLED` | Unset (defaults to `true`) | true |

---

## 2. Backend curl Smoke Tests

Replace `YOUR_SECRET` with your actual `API_SECRET` value.

```bash
BASE=https://ai-stock-bot-production.up.railway.app

# Health check
curl -s "$BASE/health" | python3 -m json.tool

# System release check (all sections should be HEALTHY or WATCH)
curl -s "$BASE/api/v1/system/release-check" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool

# Feature flags (confirm no real-send flags are true)
curl -s "$BASE/api/v1/system/flags" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool

# Notification debug (confirm legacy path is inactive)
curl -s "$BASE/api/v1/notifications/debug" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool

# Daily brief (should return 200, may be empty outside market hours)
curl -s "$BASE/api/v1/briefs/operator" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool

# Portfolio holdings
curl -s "$BASE/api/v1/portfolio/holdings" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool

# Backup list
curl -s "$BASE/api/v1/backups" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool

# Notification center inbox
curl -s "$BASE/api/v1/notifications?limit=5" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool
```

**Expected results:**
- `/health` → `{"status": "ok"}`
- `/release-check` → `overall_status: HEALTHY` or `WATCH` (never `CRITICAL`)
- `/flags` → `alpha_notifications_enabled: false`, `legacy_enabled: false`
- `/notifications/debug` → `legacy_active: false`, `alpha_real_sends: false`

---

## 3. iOS Build and Run Steps

```bash
cd InvestingApp
# Open project in Xcode
open InvestingApp.xcodeproj

# OR build from command line (requires valid signing)
xcodebuild -project InvestingApp.xcodeproj \
  -scheme InvestingApp \
  -destination 'platform=iOS Simulator,name=iPhone 16' \
  -configuration Debug \
  build
```

**Manual UI checklist:**
- [ ] App launches without crash
- [ ] Home tab shows market data cards
- [ ] Portfolio tab shows holdings (or empty state if no positions)
- [ ] Opportunities tab loads (may show empty outside market hours)
- [ ] Operator tab opens and shows sections:
  - [ ] Release check section loads
  - [ ] Backup section: can create a backup (tap "Create Backup")
  - [ ] Restore Preview: shows read-only preview, no writes
- [ ] Settings tab opens and loads API URL field
- [ ] Network requests succeed (check for auth errors in logs)

---

## 4. Manual Portfolio Correction Steps

If holdings are out of sync after migration to production:

```bash
# View current holdings
curl -s "$BASE/api/v1/portfolio/holdings" -H "Authorization: Bearer YOUR_SECRET"

# Add or correct a position (manual upsert)
curl -s -X POST "$BASE/api/v1/portfolio/manual/position" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "VFV.TO", "shares": 10, "avg_cost": 120.00, "note": "V1 correction"}'

# Deactivate a stale position
curl -s -X POST "$BASE/api/v1/portfolio/manual/deactivate" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"ticker": "WRONG_TICKER", "reason": "Duplicate entry"}'

# Set cash balance
# Via WhatsApp: send "500" to set $500 available cash
# Via API:
curl -s -X POST "$BASE/api/v1/portfolio/cash" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"amount": 500}'
```

---

## 5. Backup Creation Steps

**Before any major changes, create a backup:**

```bash
# Create full backup
curl -s -X POST "$BASE/api/v1/backups" \
  -H "Authorization: Bearer YOUR_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"backup_type": "FULL", "notes": "V1 launch backup"}'

# Verify the backup
BACKUP_ID=$(curl -s "$BASE/api/v1/backups" -H "Authorization: Bearer YOUR_SECRET" \
  | python3 -c "import sys,json; data=json.load(sys.stdin); print(data['data']['backups'][0]['backup_id'])")

curl -s "$BASE/api/v1/backups/$BACKUP_ID/verify" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool
```

**Via iOS app:**
- Operator tab → Backup section → "Create Backup" → type: FULL → tap Create
- Then tap the backup → "Verify" to confirm integrity

---

## 6. Notification Safety Checks

**Before going live, confirm:**

```bash
# Check release-check section for notification flags
curl -s "$BASE/api/v1/system/release-check" \
  -H "Authorization: Bearer YOUR_SECRET" \
  | python3 -c "
import sys, json
data = json.load(sys.stdin)
for section in data.get('sections', []):
    if 'notification' in section.get('name', '').lower():
        print(section)
"

# Check notification debug endpoint
curl -s "$BASE/api/v1/notifications/debug" \
  -H "Authorization: Bearer YOUR_SECRET" | python3 -m json.tool
```

**Green flags (safe state):**
- `LEGACY_NOTIFICATIONS_ENABLED` is `false` or unset
- `ALPHA_NOTIFICATIONS_ENABLED` is `false` or unset  
- `ALPHA_NOTIFICATIONS_DRY_RUN_ONLY` is `true` or unset
- `ALPHA_ALERTS_ENABLED` is `false` or unset
- No unexpected WhatsApp messages in the first few minutes after deploy

**Red flags (investigate immediately):**
- Release check returns `CRITICAL`
- You receive unexpected WhatsApp alerts after deploy
- `/notifications/debug` shows `legacy_active: true`

---

## 7. What to Monitor for First 3 Days

### Day 1 — Launch
- [ ] Deploy succeeds (Railway shows green)
- [ ] `/health` returns 200
- [ ] Morning summary arrives at 8:45 AM ET (weekday) — check content is correct
- [ ] No unexpected WhatsApp messages during market hours
- [ ] iOS app connects successfully

### Day 2 — Market Hours Validation
- [ ] Sell monitor job fires at 9:30 AM ET (check Railway logs)
- [ ] Scanner job fires every 30 minutes (check logs)
- [ ] No alerts fire unless you have holdings and a genuine signal exists
- [ ] Portfolio command (`PORTFOLIO`) returns correct holdings via WhatsApp

### Day 3 — End-to-End Workflow
- [ ] Record a test trade: `BOUGHT VFV.TO 1 100.00` via WhatsApp
- [ ] Verify portfolio shows the holding
- [ ] Verify sell monitor considers it in next scan
- [ ] Record the sale: `SOLD VFV.TO 1 105.00`
- [ ] Verify P&L is calculated correctly
- [ ] Create a backup after confirming data is correct

---

## 8. Rollback Plan

If anything goes wrong:
1. Go to Railway → Deployments → roll back to previous build
2. If DB is corrupted: restore from latest backup (iOS Operator → Backup → Restore Preview)
3. To disable all notifications immediately: set `LEGACY_NOTIFICATIONS_ENABLED=false`, `ALPHA_NOTIFICATIONS_ENABLED=false`, `UNIFIED_NOTIFICATIONS_ENABLED=false` in Railway environment

---

## 9. Twilio WhatsApp Sandbox Reminder

The Twilio sandbox **expires every 72 hours**. To re-join:
1. Open WhatsApp
2. Message `+1 415 523 8886`
3. Send: `join independent-dangerous`

If you stop receiving messages, this is the first thing to check.
