import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { AuthProvider } from "../auth/AuthContext";
import { SYNTHETIC_NOTICE } from "../components/Layout";
import { LoginPage } from "./LoginPage";

function renderLogin() {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AuthProvider>
        <MemoryRouter>
          <LoginPage />
        </MemoryRouter>
      </AuthProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.clear();
});

it("always shows the synthetic-data disclaimer and never displays passwords", () => {
  renderLogin();
  expect(screen.getByRole("note")).toHaveTextContent(SYNTHETIC_NOTICE);
  expect(screen.getByLabelText("Password")).toHaveValue("");
});

it("shows the server's problem detail on failed login and stores no token", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify({ title: "Unauthorized", status: 401, detail: "Invalid email or password", code: "INVALID_CREDENTIALS" }), {
      status: 401,
      headers: { "Content-Type": "application/problem+json" },
    }),
  );
  renderLogin();
  await userEvent.type(screen.getByLabelText("Password"), "wrong");
  await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Invalid email or password"));
  expect(sessionStorage.getItem("fb.token")).toBeNull();
});
