# قابلیت‌های اسکریپینگ ScrapeGPT — وضعیت فعلی و نقشه راه آینده

> این داکیومنت توضیح کاملی از نحوه کار سیستم اسکریپینگ، محدودیت‌های جاری، و اینکه در فازهای بعدی چه چیزی تغییر می‌کند، ارائه می‌دهد. اگر می‌خواهی بدانی این سیستم همان چیزی است که می‌خواهی یا نه، این سند را بخوان.

---

## ۱. چطور اسکریپینگ انجام می‌شود (pipeline فعلی — فاز ۲)

یک پروژه از ۵ مرحله عبور می‌کند:

```
URL ورودی
    ↓
[Fetch] صفحه HTML دریافت می‌شود
    ↓
[DOM Summary] ساختار صفحه فشرده می‌شود
    ↓
[AI Analysis] هوش مصنوعی فیلدها و سلکتورها را کشف می‌کند
    ↓
[User Review] کاربر فیلدها را انتخاب و ویرایش می‌کند
    ↓
[Preview] سلکتورها روی صفحه seed واقعاً اجرا می‌شوند
    ↓
[Extract] لینک‌های هم‌دامنه crawl می‌شوند و سلکتورها روی صفحات اجرا می‌شوند
    ↓
Export (CSV / JSON / XLSX)
```

### مرحله ۱ — Fetch (دریافت صفحه)

`fetch_url()` ابتدا صفحه را با **httpx** (static fetch) دریافت می‌کند. اگر محتوای HTML کمتر از ۵۰۰ کاراکتر باشد (صفحه‌ای که بدون JavaScript خالی است)، سیستم به‌صورت خودکار سراغ **Playwright/Chromium** می‌رود و صفحه را بعد از اجرای JavaScript دریافت می‌کند.

سه حالت وجود دارد:
- `AUTO` — static اول، browser اگر محتوا کم بود
- `STATIC` — فقط static (سریع‌تر، بدون Playwright)
- `BROWSER` — فقط browser rendering

محدودیت‌های این مرحله: صفحه در `MAX_FETCH_BYTES` bytes بریده می‌شود (برای صفحات خیلی بزرگ). redirect‌ها به IPs خصوصی (مثل `192.168.x.x` یا `10.x.x.x`) بلاک می‌شوند.

### مرحله ۲ — DOM Summary (فشرده‌سازی ساختار)

**مشکل مهم:** کل HTML صفحه به AI داده نمی‌شود. یک خلاصه ساختاری ساخته می‌شود تا توکن مصرفی کم باشد.

تابع `build_dom_summary()` اینها را استخراج می‌کند:
- عنوان و meta description
- حداکثر **۸ heading** (h1/h2/h3)
- داده‌های **JSON-LD** (structured data سایت)
- حداکثر **۱۵ کلاس CSS** که بیشترین تکرار دارند (احتمالاً container آیتم‌ها)
- نمونه HTML از containerهای تکراری
- نمونه tableها همراه با header/rowهای اول
- attributeهای `data-*`
- حداکثر **۱۲ لینک** نمونه
- کنترل‌های احتمالی **pagination** (دکمه‌های next/prev)
- یک snippet کوتاه از text بدنه صفحه

همه این‌ها به حداکثر **۱۰۰۰۰ کاراکتر** محدود می‌شوند.

**محدودیت باقی‌مانده:** هنوز فقط seed page تحلیل می‌شود. اگر سایت چند template متفاوت داشته باشد (مثلاً listing و detail و category)، ممکن است فیلدهای یک template دیگر دیده نشود. این موضوع برای فاز کیفیت/Template Intelligence بعدی است.

### مرحله ۳ — AI Analysis (تحلیل با هوش مصنوعی)

`analyze_page()` از LiteLLM برای تحلیل استفاده می‌کند. LiteLLM یک abstraction layer است که بیش از ۱۰۰ پروویدر (OpenAI، Anthropic، Gemini، Ollama، و غیره) را با یک API یکسان پشتیبانی می‌کند.

دو حالت تحلیل وجود دارد:

**STRUCTURED mode** — برای صفحات listing (محصولات، مقالات، جدول‌ها):
```json
{
  "page_type": "listing",
  "repeated_item_selector": ".product-card",
  "candidate_fields": [
    {
      "name": "price",
      "selector": ".price",
      "data_type": "number",
      "confidence": 0.95,
      "sample_values": ["$19.99", "$24.50"]
    }
  ],
  "pagination_selector": ".next-page",
  "estimated_pages": 34,
  "confidence": 0.87
}
```

**CONTENT mode** — برای صفحات مقاله/بلاگ/RAG:
```json
{
  "content_type": "article",
  "primary_content_selector": "article.main-content",
  "recommended_chunking": "paragraph",
  "metadata_fields": [...]
}
```

**Cache:** اگر همان صفحه با همان پروویدر/مدل قبلاً آنالیز شده باشد، AI دوباره فراخوانده نمی‌شود. کلید cache = `(content_hash, extraction_mode, provider, model, analyzer_version)`.

### مرحله ۴ — Preview و Extraction واقعی

در فاز ۲، سیستم دیگر `sample_values` هوش مصنوعی را به‌عنوان خروجی واقعی ذخیره نمی‌کند.

رفتار فعلی:

- `Preview` صفحه seed را دوباره fetch می‌کند و CSS selectorهای ذخیره‌شده را واقعاً اجرا می‌کند.
- `Extract` یک background task شروع می‌کند.
- لینک‌های هم‌دامنه از HTML کشف و normalize می‌شوند.
- هر URL در جدول `crawl_pages` ذخیره می‌شود.
- هر صفحه با همان safety path قبلی fetch می‌شود: URL validation + robots.txt + fetcher.
- selectorها روی HTML اجرا می‌شوند و خروجی در `extracted_records` ذخیره می‌شود.
- خروجی قابل دانلود به شکل CSV، JSON، و XLSX است.

در structured mode اگر AI یک `repeated_item_selector` بدهد، استخراج رکوردها بر اساس همان containerهای تکراری انجام می‌شود. اگر چنین selectorی وجود نداشته باشد، سیستم fallback index-based انجام می‌دهد.

---

## ۲. روی چه سایت‌هایی کار می‌کند؟

| نوع سایت | وضعیت فعلی | توضیح |
| -------- | ---------- | ----- |
| سایت‌های static HTML ساده | ✅ کامل | بهترین عملکرد |
| سایت‌های JS-rendered (React/Vue/Next.js) | ✅ با Playwright | نیاز به نصب `playwright install chromium` |
| سایت‌های listing (محصولات، اخبار، جداول) | ✅ STRUCTURED mode | |
| سایت‌های مقاله/بلاگ/دانشنامه | ✅ CONTENT mode | |
| سایت‌هایی با robots.txt | ✅ رعایت می‌شود | اگر مسیر disallow باشد، بلاک می‌شود |
| سایت‌هایی که HTTPS دارند | ✅ | |
| سایت‌های نیاز به لاگین/auth | ❌ پشتیبانی نمی‌شود | فاز ۶ |
| سایت‌هایی با CAPTCHA | ❌ bypass نمی‌شود | عمداً — non-goal |
| APIهای JSON (بدون HTML) | ❌ | content-type باید HTML باشد |
| فایل‌های PDF | ❌ بلاک می‌شود | content-type مجاز نیست |
| IPs خصوصی (`192.168.x.x`، `10.x.x.x`) | ❌ بلاک می‌شود | SSRF protection |
| سایت‌های با Cloudflare/bot detection | ⚠️ کار می‌کند اما ناقص | صفحه challenge برگشت داده می‌شود |
| سایت‌های چند-صفحه‌ای (pagination) | ✅ پایه‌ای | crawl هم‌دامنه تا page limit؛ template routing پیشرفته هنوز نیست |

---

## ۳. چقدر هوشمند است؟

### چه چیزهایی هوشمندانه انجام می‌شود:

- **Auto render detection:** سیستم خودش می‌فهمد صفحه نیاز به JavaScript دارد یا نه
- **CSS selector discovery:** AI سلکتورها را از روی ساختار DOM پیشنهاد می‌دهد، نه hardcode
- **Confidence scoring:** هر فیلد یک عدد بین ۰ تا ۱ دارد که نشان می‌دهد AI چقدر مطمئن است
- **Page type detection:** listing vs detail vs content به‌صورت خودکار تشخیص داده می‌شود
- **Pagination detection:** دکمه‌های next/prev خودکار کشف می‌شوند
- **Analysis cache:** همان صفحه دوباره تحلیل نمی‌شود
- **Structured + Content dual mode:** برای هر دو نوع داده (جدولی یا متن) بهینه شده

### محدودیت‌های هوشمندی:

- AI فقط DOM summary می‌بیند، نه کل صفحه — ساختارهای پیچیده ممکن است از دستش برود
- Preview و Extract با selector واقعی انجام می‌شود، اما اگر selector بد باشد، خروجی هم بد می‌شود
- برای صفحات بسیار dynamic که ساختار DOM ندارند، سلکتورها اشتباه پیشنهاد می‌شوند
- هر بار که layout سایت عوض شود، سلکتورها باید دوباره آنالیز شوند (فاز ۲: selector repair خودکار)

---

## ۴. مشکل داده‌های ناقص — دقیقاً کجاست؟

### مشکل ۱: دقت selector و templateهای متفاوت

**وضعیت فعلی:** سیستم selectorها را واقعاً اجرا می‌کند و صفحات هم‌دامنه را crawl می‌کند. مشکل اصلی دیگر "فقط نمونه AI" نیست؛ مشکل اصلی این است که selector AI ممکن است برای همه templateهای سایت درست نباشد.

**مثال:** اگر seed page یک listing باشد، selectorهای listing روی detail page ممکن است مقدار خالی یا ناقص برگردانند.

**رفع آینده:** template fingerprinting، URL pattern routing بهتر، field quality stats، و selector repair.

---

### مشکل ۲: DOM Summary خیلی محدود است — کجاها باید بهتر شود

**وضعیت فعلی:** `build_dom_summary()` در فاز ۲ بهتر شده است:
- فقط ۸ heading
- فقط ۱۲ لینک
- ۱۵ class CSS تکراری
- نمونه HTML containerهای تکراری
- نمونه table
- attributeهای `data-*`
- فقط ۶۰۰ کاراکتر متن
- کل چیزی که AI می‌بیند: ۱۰۰۰۰ کاراکتر

**ریسک باقی‌مانده:** برای صفحات پیچیده:
1. سلکتورها اشتباه هستند چون AI ساختار واقعی DOM را ندیده
2. فیلدهایی که در عمق نستینگ هستند کشف نمی‌شوند
3. جداول بزرگ یا nested structures از دید AI پنهان می‌ماند

**بهبودهای آینده DOM Summary:**

| بهبود | توضیح |
| ----- | ----- |
| **نمونه‌برداری از ساختار جدول** | اگر `<table>` وجود دارد، ۲–۳ ردیف کامل به همراه header‌ها اضافه شود |
| **بیشتر repeated containers** | از ۵ به ۱۵ افزایش پیدا کند؛ تمام containerهایی که ۳+ بار تکرار می‌شوند مهم هستند |
| **نمونه‌برداری از nested items** | اگر `.product-card` یک container است، محتوای کامل یک نمونه از آن در summary باشد |
| **attribute scanning** | `data-*` attribute‌ها اغلب حاوی داده‌های مهم (ID، قیمت، دسته‌بندی) هستند |
| **template-specific summary** | برای listing/detail/category جداگانه summary ساخته شود |
| **chunk-based analysis** | برای صفحات خیلی پیچیده، DOM summary را بخش‌بخش کن و AI چندبار صدا بزن |

---

### مشکل ۳: فقط صفحه seed، نه کل سایت

وقتی URL می‌دهی، فقط همان صفحه آنالیز می‌شود. اگر سایت pagination دارد یا ۵۰ صفحه دارد، **هیچکدام از صفحات دیگر** fetch نمی‌شوند.

**وضعیت فعلی:** BFS crawl پایه‌ای پیاده‌سازی شده است. سیستم لینک‌های هم‌دامنه را کشف می‌کند و تا `page_limit` استخراج می‌کند. concurrent workers، lease recovery واقعی بعد از crash، و template-aware crawling هنوز آینده هستند.

---

## ۵. تفاوت با Firecrawl و چرا ما می‌توانیم بهتر باشیم

| ویژگی | Firecrawl | ScrapeGPT (فاز ۲) |
| ----- | --------- | ---------------- |
| روش extraction | هر صفحه → Markdown → LLM extract | AI یک‌بار ساختار را می‌فهمد، CSS همه صفحات را extract می‌کند |
| هزینه AI | برای هر صفحه یک LLM call | فقط برای آنالیز اولیه — نه per-page |
| داده خروجی | Markdown / unstructured text | فیلدهای typed و structured (number, string, url, date) |
| raw vs normalized | ندارد | raw_data همیشه حفظ می‌شود، normalized جدا |
| self-hosted | کد باز است، اما SaaS-first | کاملاً self-hosted، BYOK |
| field selection | ندارد — کل محتوا export می‌شود | کاربر دقیقاً انتخاب می‌کند چه فیلدهایی استخراج شوند |
| data quality | بسته به LLM، متغیر | سلکتور deterministic است — همیشه همان نتیجه |
| pagination | ✅ | ✅ (فاز ۲) |
| JS rendering | ✅ | ✅ (Playwright) |

**مزیت اصلی ما:** AI فقط برای درک ساختار استفاده می‌شود. استخراج واقعی با CSS selector انجام می‌شود — سریع، ارزان، و deterministic. Firecrawl و Crawl4AI برای هر صفحه LLM صدا می‌زنند. این در مقیاس بزرگ خیلی گران‌تر و کندتر است.

---

## ۶. فازهای بعدی — چه چیزی تغییر می‌کند

### فاز ۲ — Real Extraction Engine (پیاده‌سازی شده)

این فاز سیستم را از "نمونه‌نمایی" به "استخراج واقعی" تبدیل کرد.

**چه چیزی الان build شده است:**

- **BFS crawl پایه‌ای:** از صفحه seed شروع، لینک‌های هم‌دامنه را کشف می‌کند تا `MAX_PAGES_PER_JOB` / `page_limit`
- **CSS execution واقعی:** سلکتوری که AI پیشنهاد داد روی صفحات اجرا می‌شود
- **Page-level persistence:** هر صفحه یک ردیف در دیتابیس است. این پایه crash recovery آینده است، اما resume واقعی بعد از crash هنوز کامل نشده است
- **Per-page failure isolation:** یک صفحه blocked → بقیه ادامه می‌دهند. یک job با ۱۰۰۰ صفحه که ۵ تا block شد، ۹۹۵ رکورد تحویل می‌دهد
- **Blocked/failed page tracking:** robots/URL/fetch failure در سطح صفحه ثبت می‌شود و کل پروژه را الزاماً نمی‌کشد
- **Export:** CSV / JSON / XLSX

**نتیجه:** برای یک سایت با ۵۰۰ محصول، فاز ۲ همه ۵۰۰ محصول را extract می‌کند، نه ۳ نمونه.

---

### بهبود DOM Summary (پیاده‌سازی شده، با ریسک باقی‌مانده)

این موردی است که تو مستقیماً اشاره کردی: "سیستم سامری هم باید تغییر بدیم تا اطلاعات ناقص نشه"

در فاز ۲ این موارد اضافه شد:

- نمونه HTML از repeated containers
- table samples
- `data-*` attributes
- افزایش repeated classes از ۵ به ۱۵
- افزایش سقف summary به ۱۰۰۰۰ کاراکتر

ریسک باقی‌مانده: اگر سایت چند template متفاوت داشته باشد، تحلیل یک seed page کافی نیست. این باید در فاز بعد با template intelligence و selector quality stats حل شود.

---

### فاز ۴ — AI Normalization

بعد از extraction، یک مرحله اختیاری:
- تاریخ‌ها به فرمت یکسان تبدیل می‌شوند
- قیمت‌ها parse می‌شوند (حذف `$`, `,`)
- آدرس‌ها normalize می‌شوند
- `raw_data` هرگز تغییر نمی‌کند — normalization جدا ذخیره می‌شود

---

### فاز ۶ — Authenticated Crawling (پیچیده‌ترین)

- کاربر session cookie‌های مرورگر خودش را paste می‌کند
- سیستم از آن‌ها برای authenticated requests استفاده می‌کند
- CAPTCHA **خودکار حل نمی‌شود** (این عمداً non-goal است)
- Scheduled jobs: هر X ساعت/روز سایت دوباره scrape شود (site monitoring)

---

## ۷. خلاصه: الان چه می‌تواند و چه نمی‌تواند

### می‌تواند (Phase 2 — همین الان):
- ✅ هر URL عمومی را آنالیز کند و فیلدهای قابل استخراج پیشنهاد دهد
- ✅ ساختار صفحه، نوع محتوا، و pagination را خودکار تشخیص دهد
- ✅ صفحات JavaScript-rendered را با Playwright بخواند
- ✅ preview واقعی از اجرای selector روی صفحه seed نشان دهد
- ✅ لینک‌های هم‌دامنه را تا page limit crawl کند
- ✅ CSS selector واقعی را روی صفحات اجرا کند
- ✅ رکوردهای واقعی را در دیتابیس ذخیره کند
- ✅ Cache کند تا برای URL یکسان دوباره AI فراخوانده نشود
- ✅ داده را به CSV/JSON/XLSX export کند

### نمی‌تواند / هنوز کامل نیست:
- ❌ template routing هوشمند برای سایت‌هایی با layoutهای متعدد
- ❌ selector repair خودکار
- ❌ resume کامل بعد از crash با worker lease recovery
- ❌ crawler concurrent چند-worker
- ❌ وارد سایت‌های نیاز به login شود
- ❌ CAPTCHA حل کند
- ❌ PDF یا محتوای غیر HTML بخواند
- ❌ challenge handling پیشرفته یا bypass

---

## ۸. آیا API key رایگان برای اسکریپینگ کافی است؟

این سوال مهمی است — چون سیستم ما BYOK است و کاربر باید خودش key بدهد. پاسخ واضح است: **Google AI Studio بله، OpenRouter رایگان خیر.**

---

### Google AI Studio — Gemini (رایگان)

| معیار | مشخصات |
| ----- | ------- |
| مدل پیشنهادی | Gemini 2.0 Flash / Gemini 2.5 Flash |
| RPM (درخواست در دقیقه) | ۱۵ |
| RPD (درخواست در روز) | ۱,۵۰۰ |
| TPM (توکن در دقیقه) | ۱,۰۰۰,۰۰۰ |
| Context window | ۱,۰۰۰,۰۰۰ توکن |
| JSON / Structured output | ✅ کاملاً پشتیبانی می‌شود — schema enforced |
| قابلیت اطمینان | بسیار بالا — مستقیم از Google، بدون queue |
| نیاز به کارت بانکی | ❌ ندارد |

**آیا ۱۵ RPM و ۱,۵۰۰ RPD برای اسکریپینگ کافی است؟**

ما **AI را فقط برای آنالیز اولیه** صدا می‌زنیم، نه هر صفحه. یک سایت با ۵۰۰ صفحه = **۱ تا ۳ بار AI call** (یک‌بار برای seed page، شاید یک‌بار برای template دیگر).

حساب:
- ۱۵ RPM = می‌توانی ۱۵ پروژه در ۱ دقیقه استارت بزنی
- ۱,۵۰۰ RPD = می‌توانی ۱,۵۰۰ پروژه در روز آنالیز کنی
- Selector repair (اگر فعال) = چند call اضافه در حین crawl

**نتیجه: برای استفاده شخصی و حتی تیم‌های کوچک، Google AI Studio رایگان کاملاً کافی است.**

⚠️ نقطه ضعف: اگر سرعت رو بخوای زیاد کنی و بخوای ۵۰+ پروژه همزمان استارت بزنی، به پلن پولی نیاز داری. اما برای معمول‌ترین use case ها، رایگان کار می‌کند.

---

### OpenRouter (رایگان — tier بدون شارژ)

| معیار | مشخصات |
| ----- | ------- |
| RPM (روی کاغذ) | ۲۰ |
| RPD (بدون شارژ حساب) | **۵۰ در روز** |
| RPD (بعد از ۱۰ دلار شارژ) | ۱,۰۰۰ در روز |
| JSON / Structured output | ⚠️ وابسته به مدل — ضمانت ندارد |
| خطای ۴۲۹ در عمل | 🔴 بسیار زیاد — حتی برای کاربران پولی |
| قابلیت اطمینان | ضعیف |

**مشکل اصلی OpenRouter رایگان:**
- ۵۰ RPD بیشتر از ۵۰ پروژه در روز نمی‌توانی آنالیز کنی
- خطاهای ۴۲۹ حتی در ساعات غیر شلوغ گزارش می‌شوند
- یک retry loop می‌تواند کل سهمیه روزانه را ظرف چند دقیقه تمام کند (چون حتی درخواست‌های failed هم از quota کم می‌شوند)
- مدل‌های رایگان دائماً deprecate می‌شوند (`gemini-2.0-flash-exp:free` در فوریه ۲۰۲۶ deprecated شد)
- Structured JSON output روی مدل‌های رایگان OpenRouter ناپایدار است

**نتیجه: OpenRouter رایگان برای تست و prototype مناسب است. برای هر چیزی که واقعاً بخواهی کار کند، استفاده نکن.**

---

### جدول مقایسه سریع

| معیار | Google AI Studio (رایگان) | OpenRouter (رایگان) |
| ----- | ------------------------- | ------------------- |
| مناسب production | ✅ بله | ❌ خیر |
| Structured output مطمئن | ✅ | ⚠️ ناپایدار |
| ۵۰ پروژه در روز | ✅ آسان | ❌ حد سقف |
| خطای ۴۲۹ | نادر | بسیار رایج |
| model stability | ✅ | ⚠️ مدل‌ها deprecate می‌شوند |
| توصیه | **اول انتخاب کن** | فقط برای تست |

---

### چه provider ای برای کاربران جدی توصیه می‌شود؟

| بودجه | توصیه |
| ----- | ----- |
| رایگان | **Google AI Studio** با Gemini 2.0 Flash |
| پولی کم | **OpenRouter** با ۱۰ دلار شارژ + مدل‌های paid (مثل `google/gemini-2.0-flash`) |
| پولی جدی | مستقیم از **OpenAI** یا **Anthropic** یا **Google Vertex AI** |
| محلی (بدون API) | **Ollama** با مدل‌های local مثل `llama3.2` یا `qwen2.5-coder` — رایگان اما کندتر |

> **نکته مهم برای تو:** چون می‌خواهی یک سیستم متمایز بسازی، کاربران target تو احتمالاً developer هستند که API key دارند یا می‌توانند بگیرند. Google AI Studio برای آن‌ها کاملاً رایگان و کافی است — هیچ مانعی وجود ندارد.

---

## ۹. نتیجه‌گیری: آیا این همان چیزی است که می‌خواهی؟

**اگر می‌خواهی داده‌های واقعی در مقیاس بزرگ جمع‌آوری کنی:**
→ فاز ۲ پایه واقعی این کار را ایجاد کرده است: crawl هم‌دامنه، selector execution، رکورد واقعی، export. برای production قابل اعتمادتر، فاز بعد باید quality/template intelligence بسازد.

**اگر می‌خواهی بهتر از Firecrawl باشی:**
→ مزیت اصلی ما AI-once + CSS-everywhere است. این را باید در UI و README به وضوح توضیح دهیم.

**اگر می‌خواهی داده‌ها کامل و با کیفیت بالا باشند:**
→ حالا DOM Summary بهتر شده، اما باید field quality stats، template detection، و selector repair اضافه شود.

**پیشنهاد ترتیب اولویت‌ها:**
1. Quality layer: field success rate، page/template diagnostics، selector health
2. Template intelligence و URL pattern routing بهتر
3. Visual field selection برای کم کردن نیاز به CSS
