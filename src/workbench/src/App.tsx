import {
  useEffect,
  useState,
  useRef,
  createContext,
  useContext,
  type FormEvent,
  type ReactNode,
} from "react";
import {
  Link,
  NavLink,
  Navigate,
  Route,
  Routes,
  useNavigate,
  useLocation,
} from "react-router-dom";
import {
  ArrowRight,
  ArrowUpRight,
  ArrowLeft,
  ScanLine,
  ClipboardList,
  Camera,
  FileText,
  ShieldCheck,
  Search,
  Plus,
  LogOut,
  AlertTriangle,
  Loader2,
  Eye,
  EyeOff,
  UploadCloud,
  X,
  Car,
  Layers,
  LockKeyhole,
  Mail,
  Info,
} from "lucide-react";
import {
  api,
  write,
  money,
  type User,
  type Claim,
  type ClaimList,
} from "./api";
import ClaimReview from "./ClaimReview";
const Auth = createContext<{
  user: User | null;
  setUser: (user: User | null) => void;
}>({ user: null, setUser: () => {} });
export function useAuth() {
  return useContext(Auth);
}
export function Brand({ light = false }: { light?: boolean }) {
  return (
    <Link
      to="/"
      className={`brand ${light ? "brand-light" : ""}`}
      aria-label="CLAIM-CMEV home"
    >
      <span className="brand-icon">
        <ScanLine size={23} />
      </span>
      <span>
        CLAIM<span className="brand-hyphen">-</span>CMEV
        <small>CLARITY IN EVERY CLAIM</small>
      </span>
    </Link>
  );
}
export function ErrorBanner({ message }: { message: string }) {
  return (
    <div className="error-banner" role="alert">
      <AlertTriangle size={18} />
      <span>{message}</span>
    </div>
  );
}
export function Loading() {
  return (
    <div className="loading-state" role="status">
      <Loader2 className="spin" size={26} />
      <span>Loading your workspace...</span>
    </div>
  );
}
export function Status({ value }: { value: string }) {
  return (
    <span className={`status status-${value}`}>
      {value.replaceAll("_", " ")}
    </span>
  );
}
function PublicHeader() {
  return (
    <header className="public-header">
      <div className="public-nav">
        <Brand />
        <nav aria-label="Main navigation">
          <a href="/#approach">Our approach</a>
          <a href="/#workflow">How it works</a>
          <Link to="/login" className="button button-small button-dark">
            Surveyor sign in <ArrowUpRight size={16} />
          </Link>
        </nav>
      </div>
    </header>
  );
}
function Landing() {
  return (
    <div className="public-page">
      <PublicHeader />
      <main>
        <section className="hero">
          <div className="hero-image" />
          <div className="hero-inner">
            <div className="eyebrow">
              <span className="tiny-line" /> MOTOR OWN-DAMAGE CLAIM REVIEW
            </div>
            <h1>
              Every claim.
              <br />A clearer <span>picture.</span>
            </h1>
            <p className="hero-copy">
              Bring damage photographs, marked estimates and cost references
              together. Review with context. Move forward with confidence.
            </p>
            <div className="hero-actions">
              <Link className="button button-dark" to="/login">
                Open surveyor workspace <ArrowRight size={18} />
              </Link>
              <a className="text-button" href="#workflow">
                See how it works <ArrowDownIcon />
              </a>
            </div>
            <div className="hero-assurance">
              <ShieldCheck size={18} />
              <span>AI-assisted review. Surveyor-led decisions.</span>
            </div>
          </div>
          <div className="hero-note">
            <span className="note-icon">
              <ScanLine size={21} />
            </span>
            <div>
              <strong>Evidence in focus.</strong>
              <small>Your judgement at the centre.</small>
            </div>
          </div>
          <div className="hero-bottom">
            <span>PHOTOGRAPHS</span>
            <i />
            <span>MARKED ESTIMATES</span>
            <i />
            <span>ONE CONNECTED REVIEW</span>
          </div>
        </section>
        <section className="approach section-wrap" id="approach">
          <div className="section-heading">
            <div>
              <div className="eyebrow">A CONNECTED VIEW</div>
              <h2>
                Less searching.
                <br />
                More understanding.
              </h2>
            </div>
            <p>
              The evidence you need, in the context that matters.
              <br />
              From the first photograph to the reviewed report.
            </p>
          </div>
          <div className="feature-grid">
            <article className="feature-card">
              <span className="feature-icon">
                <Camera size={25} />
              </span>
              <span className="feature-number">01</span>
              <h3>See the damage</h3>
              <p>
                Bring vehicle photographs into one place, with part and damage
                observations linked to the evidence.
              </p>
              <span className="feature-foot">
                Photo evidence <ArrowUpRight size={16} />
              </span>
            </article>
            <article className="feature-card">
              <span className="feature-icon amber">
                <FileText size={25} />
              </span>
              <span className="feature-number">02</span>
              <h3>Read the intent</h3>
              <p>
                Review extracted estimate rows and pen marks. Confirm exclusions
                and enter revised amounts yourself.
              </p>
              <span className="feature-foot">
                Marked estimates <ArrowUpRight size={16} />
              </span>
            </article>
            <article className="feature-card">
              <span className="feature-icon blue">
                <Layers size={25} />
              </span>
              <span className="feature-number">03</span>
              <h3>Connect the evidence</h3>
              <p>
                Compare each line item with the photographs and synthetic cost
                references in a single review.
              </p>
              <span className="feature-foot">
                Consolidated review <ArrowUpRight size={16} />
              </span>
            </article>
          </div>
        </section>
        <section id="workflow" className="workflow">
          <div className="section-wrap workflow-inner">
            <div className="workflow-intro">
              <div className="eyebrow">MADE FOR YOUR WORKFLOW</div>
              <h2>
                From evidence
                <br />
                to a reviewed report.
              </h2>
              <p>A practical companion to the way surveyors already work.</p>
              <Link to="/login" className="text-button">
                Enter the workspace <ArrowRight size={17} />
              </Link>
            </div>
            <div className="workflow-steps">
              {[
                {
                  title: "Upload the claim",
                  copy: "Add vehicle details, damage photographs and the marked workshop estimate.",
                },
                {
                  title: "Review with context",
                  copy: "Confirm marks, inspect findings and record your judgement alongside the original evidence.",
                },
                {
                  title: "Finalize and print",
                  copy: "Freeze the reviewed revision and prepare a clear PDF report for the claim handler.",
                },
              ].map((step, i) => (
                <div className="workflow-step" key={step.title}>
                  <span>0{i + 1}</span>
                  <div>
                    <h3>{step.title}</h3>
                    <p>{step.copy}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </section>
        <section className="principles section-wrap">
          <ShieldCheck size={30} />
          <div>
            <h3>Assistance you can put in perspective.</h3>
            <p>
              Uncertain evidence stays uncertain. Human actions stay visible.
              Final claim approval remains with the claim handler.
            </p>
          </div>
          <span className="outline-label">HUMAN IN THE LOOP</span>
        </section>
      </main>
      <footer className="public-footer">
        <Brand />
        <p>A research prototype for evidence-led claim review.</p>
        <span>CLAIM-CMEV &copy; 2026</span>
      </footer>
    </div>
  );
}
function ArrowDownIcon() {
  return <ArrowRight size={16} style={{ transform: "rotate(90deg)" }} />;
}
function Login() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("surveyor@claim-cmev.demo"),
    [password, setPassword] = useState("Demo2026!"),
    [visible, setVisible] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  if (user) return <Navigate to="/claims" replace />;
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const result = await write<{ user: User }>("/auth/login", {
        email,
        password,
      });
      setUser(result.user);
      navigate("/claims");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign in failed.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="login-page">
      <PublicHeader />
      <main className="login-layout">
        <section className="login-story">
          <div className="eyebrow">THE SURVEYOR WORKSPACE</div>
          <h1>
            A clearer view.
            <br />A considered decision.
          </h1>
          <p>
            Your photographs, estimates and findings.
            <br />
            All together, ready for your review.
          </p>
          <div className="login-story-note">
            <ShieldCheck size={21} />
            <span>Evidence-led. Human-reviewed.</span>
          </div>
        </section>
        <section className="login-card">
          <span className="login-lock">
            <LockKeyhole size={24} />
          </span>
          <div className="eyebrow">WELCOME TO CLAIM-CMEV</div>
          <h2>Sign in to your workspace</h2>
          <p>Pick up where your last review left off.</p>
          <form onSubmit={submit}>
            {error && <ErrorBanner message={error} />}
            <label>
              Email address
              <div className="input-icon">
                <Mail size={18} />
                <input
                  type="email"
                  autoComplete="username"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </label>
            <label>
              Password
              <div className="input-icon">
                <LockKeyhole size={18} />
                <input
                  type={visible ? "text" : "password"}
                  autoComplete="current-password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="icon-button"
                  aria-label={visible ? "Hide password" : "Show password"}
                  onClick={() => setVisible(!visible)}
                >
                  {visible ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>
            </label>
            <button className="button button-dark login-submit" disabled={busy}>
              {busy ? (
                <Loader2 className="spin" size={18} />
              ) : (
                <>
                  Sign in <ArrowRight size={18} />
                </>
              )}
            </button>
          </form>
          <div className="demo-login">
            <Info size={17} />
            <div>
              <strong>Explore the demonstration</strong>
              <p>
                Demo credentials are filled in for you. Claims and processing
                results are illustrative.
              </p>
            </div>
          </div>
          <Link to="/" className="login-back">
            <ArrowLeft size={15} /> Back to overview
          </Link>
        </section>
      </main>
      <div className="login-footer">
        CLAIM-CMEV &nbsp; / &nbsp; SURVEYOR CONSOLE{" "}
        <span>Research prototype</span>
      </div>
    </div>
  );
}
function Shell({ children }: { children: ReactNode }) {
  const { user, setUser } = useAuth();
  const [error, setError] = useState("");
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      setUser(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign out failed");
    }
  }
  return (
    <div className="app-shell">
      <header className="app-header">
        <Brand light />
        <span className="console-label">SURVEYOR CONSOLE</span>
        <nav>
          <NavLink to="/claims">
            <ClipboardList size={18} /> Claim queue
          </NavLink>
          <div className="user-block">
            <span className="avatar">{user?.initials}</span>
            <span>
              {user?.name}
              <small>Surveyor</small>
            </span>
          </div>
          <button
            className="icon-button logout"
            title="Sign out"
            aria-label="Sign out"
            onClick={logout}
          >
            <LogOut size={18} />
          </button>
        </nav>
      </header>
      {error && <ErrorBanner message={error} />}
      <main className="workspace">{children}</main>
      <footer className="app-footer">
        <span>
          <span className="demo-dot" /> Demonstration workspace{" "}
          <span className="footer-divider">/</span> Illustrative claims &amp;
          mocked processing
        </span>
        <span>
          CLAIM-CMEV <span className="footer-divider">/</span> Evidence-led.
          Human-reviewed.
        </span>
      </footer>
    </div>
  );
}
function Protected({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  return user ? <Shell>{children}</Shell> : <Navigate to="/login" replace />;
}
function Dashboard() {
  const [data, setData] = useState<ClaimList | null>(null),
    [error, setError] = useState(""),
    [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all");
  const navigate = useNavigate();
  const load = () => {
    setError("");
    api<ClaimList>("/claims")
      .then(setData)
      .catch((e) => setError(e.message));
  };
  useEffect(load, []);
  if (!data)
    return error ? (
      <div className="empty-state">
        <ErrorBanner message={error} />
        <button className="button button-dark" onClick={load}>
          Try again
        </button>
      </div>
    ) : (
      <Loading />
    );
  const claims = data.items.filter(
    (c) =>
      (filter === "all" || c.status === filter) &&
      `${c.reference} ${c.policy_number} ${c.vehicle.plate} ${c.vehicle.make} ${c.vehicle.model} ${c.workshop}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  return (
    <>
      <div className="page-top">
        <div>
          <div className="eyebrow">MOTOR OWN-DAMAGE</div>
          <h1>
            Claim queue<span className="title-dot">.</span>
          </h1>
          <p>Your assigned inspections, ready for the next step.</p>
        </div>
        <Link className="button button-dark" to="/claims/new">
          <Plus size={18} /> New claim
        </Link>
      </div>
      <div className="stat-grid">
        <Stat
          icon={<ClipboardList size={23} />}
          label="Open claims"
          value={data.stats.open_claims}
          foot="Awaiting your next action"
        />
        <Stat
          icon={<AlertTriangle size={23} />}
          label="Open findings"
          value={data.stats.open_findings}
          foot="Across your assigned claims"
          amber
        />
        <Stat
          icon={<Camera size={23} />}
          label="Photographs received"
          value={data.stats.photographs_received}
          foot="Evidence, all in one place"
        />
      </div>
      <section className="queue-section">
        <div className="queue-toolbar">
          <div
            className="queue-tabs"
            role="group"
            aria-label="Filter claim status"
          >
            <button
              className={filter === "all" ? "active" : ""}
              onClick={() => setFilter("all")}
            >
              All claims <span>{data.total}</span>
            </button>
            <button
              className={filter === "in_review" ? "active" : ""}
              onClick={() => setFilter("in_review")}
            >
              In review
            </button>
            <button
              className={filter === "ready_to_print" ? "active" : ""}
              onClick={() => setFilter("ready_to_print")}
            >
              Ready to print
            </button>
          </div>
          <div className="search-field">
            <Search size={18} />
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search claim, policy or plate"
              aria-label="Search claims"
            />
            {query && (
              <button
                className="icon-button"
                onClick={() => setQuery("")}
                aria-label="Clear search"
              >
                <X size={15} />
              </button>
            )}
          </div>
        </div>
        <div className="claim-table-wrap">
          <table className="claim-table">
            <thead>
              <tr>
                <th>Claim</th>
                <th>Vehicle &amp; workshop</th>
                <th>Inputs</th>
                <th>Estimate</th>
                <th>
                  <span className="sr-only">Open claim</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {claims.map((c) => (
                <tr
                  key={c.claim_id}
                  onClick={() => navigate(`/claims/${c.claim_id}/review`)}
                >
                  <td>
                    <Link
                      className="claim-ref"
                      to={`/claims/${c.claim_id}/review`}
                    >
                      {c.reference}
                    </Link>
                    <p className="claim-subline">
                      {c.surveyor} <span>&middot;</span> {c.policy_number}
                    </p>
                    <Status value={c.status} />
                  </td>
                  <td>
                    <strong className="vehicle-name">
                      {c.vehicle.make} {c.vehicle.model}{" "}
                      <span>({c.vehicle.year})</span>
                    </strong>
                    <p className="plate">{c.vehicle.plate}</p>
                    <p>{c.workshop}</p>
                  </td>
                  <td>
                    <p className="input-count">
                      <Camera size={14} />
                      {c.photograph_count} photographs
                    </p>
                    <p className="input-count">
                      <FileText size={14} />
                      {c.estimate_row_count} estimate rows
                    </p>
                    <p className="loss-date">Loss {c.loss_date}</p>
                  </td>
                  <td>
                    <strong className="estimate-value">
                      {money(c.declared_total)}
                    </strong>
                    {c.finding_count > 0 ? (
                      <p className="finding-count">
                        <span />
                        {c.finding_count} findings
                      </p>
                    ) : (
                      <p className="subtle">
                        {c.status === "awaiting_upload"
                          ? "Awaiting evidence"
                          : "No open findings"}
                      </p>
                    )}
                  </td>
                  <td>
                    <ArrowRight size={20} className="row-arrow" />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!claims.length && (
            <div className="empty-state">
              <Search size={28} />
              <h3>No matching claims</h3>
              <p>Try a different search or status filter.</p>
              <button
                className="text-button"
                onClick={() => {
                  setQuery("");
                  setFilter("all");
                }}
              >
                Clear filters <ArrowRight size={16} />
              </button>
            </div>
          )}
        </div>
        <div className="table-footer">
          <span>
            Showing {claims.length} of {data.total} claims
          </span>
          <span>
            <LockKeyhole size={13} /> Assigned to your workspace
          </span>
        </div>
      </section>
      <div className="queue-note">
        <ShieldCheck size={17} />
        <p>
          Findings support your review. They do not determine final claim
          approval.
        </p>
      </div>
    </>
  );
}
function Stat({
  icon,
  label,
  value,
  foot,
  amber = false,
}: {
  icon: ReactNode;
  label: string;
  value: number;
  foot: string;
  amber?: boolean;
}) {
  return (
    <article className="stat-card">
      <span className={`stat-icon ${amber ? "amber" : ""}`}>{icon}</span>
      <div>
        <span className="stat-label">{label}</span>
        <strong>{value.toString().padStart(2, "0")}</strong>
        <p>{foot}</p>
      </div>
      <span className="stat-line" />
    </article>
  );
}
function NewClaim() {
  const navigate = useNavigate();
  // Keep the same keys when an acknowledged response is lost and intake is retried.
  const requestKeys = useRef(new Map<string, string>());
  function save<T>(path: string, body: unknown): Promise<T> {
    const payload = JSON.stringify(body);
    const signature = path + payload;
    let key = requestKeys.current.get(signature);
    if (!key) {
      key = crypto.randomUUID();
      requestKeys.current.set(signature, key);
    }
    return api<T>(path, {
      method: "POST",
      body: payload,
      headers: { "Idempotency-Key": key },
    });
  }
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [progress, setProgress] = useState(""),
    [photos, setPhotos] = useState<File[]>([]),
    [estimates, setEstimates] = useState<File[]>([]);
  const [createdId, setCreatedId] = useState<string | null>(null);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    if (!photos.length && !estimates.length) {
      setError(
        "Add at least one damage photograph or estimate page to start processing.",
      );
      return;
    }
    setBusy(true);
    const form = new FormData(event.currentTarget);
    try {
      setProgress("Creating your claim...");
      let id = createdId;
      if (!id) {
        const claim = await save<Claim>("/claims", {
          reference: form.get("reference"),
          vehicle_make: form.get("vehicle_make"),
          vehicle_model: form.get("vehicle_model"),
          vehicle_year: Number(form.get("vehicle_year")),
          vehicle_class: form.get("vehicle_class"),
          plate: form.get("plate"),
          policy_number: form.get("policy_number"),
          workshop: form.get("workshop"),
          loss_date: form.get("loss_date"),
          currency: "SGD",
        });
        id = claim.claim_id;
        setCreatedId(id);
      }
      const ids: string[] = [];
      for (const [role, files] of [
        ["photograph", photos],
        ["estimate_page", estimates],
      ] as const) {
        if (!files.length) continue;
        setProgress(
          `Uploading ${role === "photograph" ? "photographs" : "estimate pages"}...`,
        );
        for (const file of files) {
          const upload = new FormData();
          upload.set("role", role);
          upload.append("files", file);
          const uploaded = await api<{
            items?: { file_id: string }[];
            files?: { file_id: string }[];
            file_ids?: string[];
          }>(`/claims/${id}/files`, {
            method: "POST",
            body: upload,
            headers: { "Idempotency-Key": crypto.randomUUID() },
          });
          ids.push(
            ...(uploaded.file_ids ??
              (uploaded.items ?? uploaded.files ?? []).map((f) => f.file_id)),
          );
        }
      }
      setProgress("Starting the evidence review...");
      await save(`/claims/${id}/input-revisions`, {
        file_ids: [...new Set(ids)],
      });
      navigate(`/claims/${id}/review`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unable to create the claim.");
    } finally {
      setBusy(false);
    }
  }
  return (
    <>
      <Link className="back-link" to="/claims">
        <ArrowLeft size={16} /> Claim queue
      </Link>
      <div className="page-top">
        <div>
          <div className="eyebrow">START A REVIEW</div>
          <h1>
            New claim<span className="title-dot">.</span>
          </h1>
          <p>Give your evidence a place to come together.</p>
        </div>
        <span className="outline-label">STEP 01 / UPLOAD</span>
      </div>
      <form className="intake-form" onSubmit={submit}>
        {error && <ErrorBanner message={error} />}
        <section className="panel">
          <div className="panel-heading">
            <span className="section-icon">
              <Car size={20} />
            </span>
            <div>
              <h2>Claim &amp; vehicle</h2>
              <p>The essentials for a clear, traceable review.</p>
            </div>
          </div>
          <div className="form-grid">
            <label>
              Claim reference{" "}
              <input
                name="reference"
                required
                placeholder="e.g. CLM-24022"
                disabled={!!createdId}
              />
            </label>
            <label>
              Policy number
              <input name="policy_number" placeholder="e.g. MOT/2026/884214" />
            </label>
            <label>
              Vehicle make
              <input name="vehicle_make" required placeholder="e.g. Toyota" />
            </label>
            <label>
              Vehicle model
              <input
                name="vehicle_model"
                required
                placeholder="e.g. Corolla Altis"
              />
            </label>
            <label>
              Year
              <input
                name="vehicle_year"
                type="number"
                min="1950"
                max="2100"
                required
                placeholder="2021"
              />
            </label>
            <label>
              Registration plate
              <input name="plate" required placeholder="e.g. SJB 4412 K" />
            </label>
            <label>
              Vehicle class
              <select name="vehicle_class" defaultValue="unknown">
                <option value="compact-sedan">Compact sedan</option>
                <option value="suv">SUV</option>
                <option value="hatchback">Hatchback</option>
                <option value="unknown">Not established</option>
              </select>
            </label>
            <label>
              Workshop
              <input name="workshop" placeholder="Workshop name" />
            </label>
            <label>
              Date of loss
              <input name="loss_date" type="date" required />
            </label>
            <label>
              Currency
              <input value="SGD — Singapore dollar" readOnly />
              <small>Fixed single-part cost basis</small>
            </label>
          </div>
        </section>
        <section className="panel">
          <div className="panel-heading">
            <span className="section-icon">
              <UploadCloud size={20} />
            </span>
            <div>
              <h2>Evidence files</h2>
              <p>
                Original files are preserved alongside the processed results.
              </p>
            </div>
          </div>
          <div className="upload-grid">
            <FilePicker
              title="Damage photographs"
              detail="JPEG or PNG · up to 40 photographs"
              files={photos}
              setFiles={setPhotos}
              accept="image/jpeg,image/png"
              limit={40}
            />
            <FilePicker
              title="Marked estimate"
              detail="JPEG, PNG or PDF · up to 10 pages"
              files={estimates}
              setFiles={setEstimates}
              accept="image/jpeg,image/png,application/pdf"
              limit={10}
            />
          </div>
          <div className="inline-info">
            <Info size={16} />
            <span>
              Processing in this workspace produces labelled demo results.
              Uploaded evidence is not analysed by a trained model.
            </span>
          </div>
        </section>
        <div className="form-actions">
          <Link to="/claims" className="button button-outline">
            Cancel
          </Link>
          <button className="button button-dark" disabled={busy}>
            {busy ? (
              <>
                <Loader2 className="spin" size={17} />
                {progress}
              </>
            ) : (
              <>
                Upload &amp; start review <ArrowRight size={17} />
              </>
            )}
          </button>
        </div>
      </form>
    </>
  );
}
function FilePicker({
  title,
  detail,
  files,
  setFiles,
  accept,
  limit,
}: {
  title: string;
  detail: string;
  files: File[];
  setFiles: (f: File[]) => void;
  accept: string;
  limit: number;
}) {
  const [error, setError] = useState("");
  function add(incoming: FileList | null) {
    if (!incoming) return;
    const list = Array.from(incoming);
    if (list.some((f) => !accept.split(",").includes(f.type))) {
      setError("Choose only the supported file types.");
      return;
    }
    if (files.length + list.length > limit) {
      setError(`Choose up to ${limit} files.`);
      return;
    }
    if (list.some((f) => f.size > 25 * 1024 * 1024)) {
      setError("Each file must be 25 MB or smaller.");
      return;
    }
    setError("");
    setFiles([...files, ...list]);
  }
  return (
    <div>
      <label
        className="upload-zone"
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          add(e.dataTransfer.files);
        }}
      >
        <span>
          <UploadCloud size={24} />
        </span>
        <strong>{title}</strong>
        <p>
          <b>Choose files</b> or drag them here
        </p>
        <small>{detail}</small>
        <input
          type="file"
          multiple
          accept={accept}
          onChange={(e) => {
            add(e.target.files);
            e.target.value = "";
          }}
        />
      </label>
      {error && (
        <p className="field-error" role="alert">
          {error}
        </p>
      )}
      {files.map((f, i) => (
        <div className="upload-file" key={`${f.name}-${i}`}>
          <FileText size={16} />
          <span>
            {f.name}
            <small>{(f.size / 1024 / 1024).toFixed(1)} MB</small>
          </span>
          <button
            type="button"
            className="icon-button"
            aria-label={`Remove ${f.name}`}
            onClick={() => setFiles(files.filter((_, j) => j !== i))}
          >
            <X size={16} />
          </button>
        </div>
      ))}
    </div>
  );
}
function NotFound() {
  return (
    <div className="empty-state">
      <ScanLine size={36} />
      <h1>Page not found</h1>
      <p>This page may have moved, or the link is incomplete.</p>
      <Link className="button button-dark" to="/claims">
        Back to your workspace <ArrowRight size={18} />
      </Link>
    </div>
  );
}
export default function App() {
  const [user, setUser] = useState<User | null>(null),
    [ready, setReady] = useState(false);
  useEffect(() => {
    api<{ user: User }>("/auth/me")
      .then((r) => setUser(r.user))
      .catch(() => setUser(null))
      .finally(() => setReady(true));
  }, []);
  const location = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
  }, [location.pathname]);
  if (!ready) return <Loading />;
  return (
    <Auth.Provider value={{ user, setUser }}>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/login" element={<Login />} />
        <Route
          path="/claims"
          element={
            <Protected>
              <Dashboard />
            </Protected>
          }
        />
        <Route
          path="/claims/new"
          element={
            <Protected>
              <NewClaim />
            </Protected>
          }
        />
        <Route
          path="/claims/:id/*"
          element={
            <Protected>
              <ClaimReview />
            </Protected>
          }
        />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </Auth.Provider>
  );
}
