import { readFileSync } from "node:fs";
import path from "node:path";
import { expect, test, type Browser, type Locator, type Page } from "@playwright/test";

// AT-12: the flagship scenario driven entirely through the UI, in fixture mode, against a running stack.
// Passwords come from FB_DEMO_PASSWORD, E2E_CREDENTIALS or the gitignored seed output; they are never logged.

const ROOT = path.resolve(process.cwd(), "..");
const FIXTURES = path.join(ROOT, "demo", "fixtures");
const SCENARIO = JSON.parse(readFileSync(path.join(FIXTURES, "flagship_scenario.json"), "utf-8")) as {
  resolutions: { node: string; note: string; content: Record<string, unknown> }[];
};

function loadCredentials(): Record<string, string> {
  const shared = process.env.FB_DEMO_PASSWORD;
  if (shared) {
    const roles = ["customer", "engineer", "engineer2", "approver", "approver2", "admin"];
    return Object.fromEntries(roles.map((r) => [`${r}@demo.example`, shared]));
  }
  const file = process.env.E2E_CREDENTIALS ?? path.join(ROOT, "backend", "var", "demo_credentials.txt");
  const out: Record<string, string> = {};
  for (const line of readFileSync(file, "utf-8").split(/\r?\n/)) {
    const parts = line.trim().split(/\s+/);
    if (parts.length === 2) out[parts[0]] = parts[1];
  }
  return out;
}
const CREDS = loadCredentials();

async function signIn(browser: Browser, email: string): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto("/login");
  await expect(page.getByText("SYNTHETIC DATA / SIMULATION — NOT MANUFACTURING CERTIFICATION").first()).toBeVisible();
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(CREDS[email] ?? "");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Projects" })).toBeVisible();
  return page;
}

const banner = (page: Page) => page.getByTestId("readiness-banner");

async function requestApproval(engineer: Page, pid: string): Promise<string> {
  await engineer.goto(`/projects/${pid}/approvals`);
  await engineer.getByLabel("Note to approver").fill("Please review the current baseline");
  await engineer.getByRole("button", { name: "Request approval" }).click();
  const row = engineer.getByTestId("approval").filter({ hasText: "PENDING" });
  await expect(row).toBeVisible();
  const assigned = (await row.getByText(/Assigned to/).textContent()) ?? "";
  const email = /Assigned to (\S+@\S+?)(?:\s|·|$)/.exec(assigned)?.[1];
  expect(email, "assigned approver email").toBeTruthy();
  return email!;
}

async function approve(approver: Page, pid: string) {
  await approver.goto(`/projects/${pid}/approvals`);
  const row = approver.getByTestId("approval").filter({ hasText: "PENDING" });
  await row.getByLabel("Decision reason").fill("Reviewed baseline, artifacts and exceptions");
  await row.getByRole("button", { name: "Approve this exact baseline" }).click();
  await expect(banner(approver)).toContainText("Released for this exact baseline");
}

async function proposeAndPublish(engineer: Page, pid: string, edits: Record<string, Record<string, string>>, reason: string) {
  await engineer.goto(`/projects/${pid}/changes/new`);
  for (const [line, fields] of Object.entries(edits)) {
    for (const [field, value] of Object.entries(fields)) await engineer.getByLabel(`${line} ${field}`).fill(value);
  }
  await engineer.getByLabel("Reason (recorded in the audit log)").fill(reason);
  await engineer.getByRole("button", { name: "Propose and preview impact" }).click();
  await expect(engineer.getByText("Preview only — nothing has changed yet")).toBeVisible();
  await expect(engineer.getByTestId("impact-row").first()).toBeVisible();
  const rows = await engineer.getByTestId("impact-row").allTextContents();
  await engineer.getByRole("button", { name: /^Publish as R\d+$/ }).click();
  await expect(engineer.getByText(/Published\. Readiness is now/)).toBeVisible();
  return rows;
}

function exceptionCard(page: Page, node: string): Locator {
  return page.getByTestId("exception").filter({ has: page.getByText(node, { exact: true }) });
}

test("AT-12 flagship: R1 READY -> 48V change BLOCKS -> evidence-backed resolution -> R3 READY", async ({ browser }) => {
  // 1. Customer creates the project and uploads the synthetic requirements PDF.
  const customer = await signIn(browser, "customer@demo.example");
  await customer.getByRole("button", { name: "New project" }).click();
  await customer.getByLabel("Name").fill(`Warehouse Scout E2E ${Date.now()}`);
  await customer.getByRole("button", { name: "Create project" }).click();
  await expect(customer).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  const pid = /projects\/([0-9a-f-]{36})/.exec(customer.url())![1];
  await expect(banner(customer)).toContainText("No baseline yet");
  await customer.getByRole("link", { name: "Documents" }).click();
  await customer.getByLabel("Requirements file").setInputFiles(path.join(FIXTURES, "requirements_v1.pdf"));
  await expect(customer.getByText("requirements_v1.pdf queued for processing.")).toBeVisible();

  // 2. Engineer reviews every extracted value against its cited evidence and publishes R1.
  const engineer = await signIn(browser, "engineer@demo.example");
  await engineer.goto(`/projects/${pid}/documents`);
  await engineer.getByRole("button", { name: "Review extraction" }).click({ timeout: 90_000 });
  await expect(engineer.getByText("Fixture mode: values were produced by a deterministic parser")).toBeVisible();
  await expect(engineer.getByTestId("candidate").first()).toBeVisible();
  await engineer.getByRole("button", { name: "Accept all verified" }).click();
  await engineer.getByRole("button", { name: "Publish reviewed values as R1" }).click();
  await expect(engineer).toHaveURL(/\/bom$/);
  await expect(banner(engineer)).toContainText("Baseline R1");

  // 3. Apply the manufacturing plan; the assigned approver releases R1.
  await engineer.getByRole("link", { name: "Overview" }).click();
  await engineer.getByRole("button", { name: "Apply Warehouse Scout plan template" }).click();
  await expect(banner(engineer)).toContainText("awaiting approval of the current baseline");
  const approverEmail = await requestApproval(engineer, pid);
  const approver = await signIn(browser, approverEmail);
  await approve(approver, pid);

  // 4. EC-001: 24V / M4-40 actuator -> 48V ACT-200 rev B with M6-50 mount. Publishing BLOCKS readiness.
  const impact = await proposeAndPublish(
    engineer,
    pid,
    {
      ACTUATOR_MAIN: {
        "part number": "ACT-200",
        revision: "B",
        attributes: JSON.stringify({ voltage_v: 48, mount_pattern: "M6-50", connector: "JST-X" }),
      },
    },
    "Customer revised drive actuator to 48V ACT-200 rev B (synthetic)",
  );
  for (const node of [
    "SUPPLIER_SELECTION:ACTUATOR_MAIN",
    "COMPATIBILITY_CHECK:MOTOR_VOLTAGE",
    "COMPATIBILITY_CHECK:ACTUATOR_MOUNT",
    "WORK_INSTRUCTION:INSTALL_ACTUATOR",
    "QC_PLAN:MOTOR_FUNCTION_TEST",
    "READINESS_APPROVAL:PROJECT",
  ]) {
    expect(impact.some((r) => r.includes(node)), `impact includes ${node}`).toBe(true);
  }
  expect(impact.some((r) => r.includes("WORK_INSTRUCTION:PACKAGE_BATTERY")), "battery packaging unaffected").toBe(false);
  await expect(banner(engineer)).toContainText("Not releasable");
  await expect(banner(engineer)).toContainText("Baseline R2");
  await engineer.getByRole("link", { name: "Approvals" }).click();
  await expect(engineer.getByText("This approval can never release a later baseline.")).toBeVisible();

  // 5. EC-002: move controller, battery and chassis to the 48V / M6-50 platform (R3).
  await proposeAndPublish(
    engineer,
    pid,
    {
      CONTROLLER: {
        "part number": "CTRL-48",
        revision: "A",
        attributes: JSON.stringify({ output_voltage_v: 48, input_voltage_v: 48, connector: "JST-X" }),
      },
      BATTERY: { "part number": "BAT-48", revision: "A", attributes: JSON.stringify({ voltage_v: 48 }) },
      CHASSIS: { "part number": "CHS-02", revision: "A", attributes: JSON.stringify({ accepted_mounts: ["M4-40", "M6-50"] }) },
    },
    "Move controller, battery and chassis to the 48V / M6-50 platform (synthetic)",
  );
  await expect(banner(engineer)).toContainText("Baseline R3");

  // 6. Resolve every exception with a new, server-validated artifact revision.
  await engineer.getByRole("link", { name: /^Exceptions/ }).click();
  await expect(engineer.getByTestId("exception").first()).toBeVisible();
  for (const res of SCENARIO.resolutions) {
    // One node can carry an exception per published change; resolving it closes all of them.
    const all = exceptionCard(engineer, res.node);
    if ((await all.count()) === 0) continue;
    const card = all.first();
    await card.getByRole("button", { name: "Resolve with evidence" }).click();
    const type = res.node.split(":")[0];
    if (type === "SUPPLIER_SELECTION") {
      await card.getByRole("combobox").selectOption(String(res.content.supplier_option_code));
    } else if (type === "WORK_INSTRUCTION") {
      await card.getByLabel("Artifact content").fill(JSON.stringify(res.content, null, 2));
    } else if (type === "QC_PLAN") {
      // The refresh helper re-pins part references and the work-instruction revision but not check expectations,
      // so the server must reject the 24 V supply check until the engineer corrects it.
      await card.getByRole("button", { name: "Refresh pinned references from current baseline" }).click();
      await card.getByLabel("Resolution note").fill(res.note);
      await card.getByRole("button", { name: "Submit revalidation" }).click();
      await expect(card.getByRole("alert")).toContainText("QC_CHECK_STALE");
      const draft = JSON.parse(await card.getByLabel("Artifact content").inputValue()) as {
        checks: { parameter?: string; expected: unknown }[];
      };
      for (const c of draft.checks) if (c.parameter === "voltage_v") c.expected = 48;
      await card.getByLabel("Artifact content").fill(JSON.stringify(draft, null, 2));
    }
    await card.getByLabel("Resolution note").fill(res.note);
    await card.getByRole("button", { name: "Submit revalidation" }).click();
    await expect(all).toHaveCount(0);
  }
  await expect(engineer.getByText("No open exceptions.")).toBeVisible();

  // 7. Re-approve the new baseline; readiness returns to READY for R3 only.
  await engineer.getByRole("link", { name: "Overview" }).click();
  await expect(banner(engineer)).toContainText("awaiting approval of the current baseline");
  const approver2Email = await requestApproval(engineer, pid);
  const reapprover = approver2Email === approverEmail ? approver : await signIn(browser, approver2Email);
  await approve(reapprover, pid);
  await expect(banner(reapprover)).toContainText("Baseline R3");

  // 8. The customer sees READY (redacted view) and the audit trail is visible to staff.
  await customer.goto(`/projects/${pid}`);
  await expect(banner(customer)).toContainText("Released for this exact baseline");
  await engineer.goto(`/projects/${pid}/audit`);
  await expect(engineer.getByRole("row").nth(10)).toBeVisible();
});
