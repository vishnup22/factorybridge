import { Navigate, Outlet, type RouteObject } from "react-router";
import { useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { Loading } from "./components/ui";
import { LoginPage } from "./pages/LoginPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { ApprovalsPage } from "./pages/project/ApprovalsPage";
import { AuditPage } from "./pages/project/AuditPage";
import { BomPage } from "./pages/project/BomPage";
import { ChangeDetailPage } from "./pages/project/ChangeDetailPage";
import { ChangeEditorPage } from "./pages/project/ChangeEditorPage";
import { ChangesPage } from "./pages/project/ChangesPage";
import { DocumentsPage } from "./pages/project/DocumentsPage";
import { ExceptionsPage } from "./pages/project/ExceptionsPage";
import { ExtractionReviewPage } from "./pages/project/ExtractionReviewPage";
import { GraphPage } from "./pages/project/GraphPage";
import { OverviewPage } from "./pages/project/OverviewPage";
import { ProjectLayout } from "./pages/project/ProjectLayout";

function RequireAuth() {
  const { me, loading } = useAuth();
  if (loading) return <Loading label="Restoring session…" />;
  if (!me) return <Navigate to="/login" replace />;
  return <Outlet />;
}

export const routes: RouteObject[] = [
  { path: "/login", element: <LoginPage /> },
  {
    element: <RequireAuth />,
    children: [
      {
        element: <Layout />,
        children: [
          { index: true, element: <ProjectsPage /> },
          {
            path: "projects/:pid",
            element: <ProjectLayout />,
            children: [
              { index: true, element: <OverviewPage /> },
              { path: "documents", element: <DocumentsPage /> },
              { path: "documents/:docId/review", element: <ExtractionReviewPage /> },
              { path: "bom", element: <BomPage /> },
              { path: "changes", element: <ChangesPage /> },
              { path: "changes/new", element: <ChangeEditorPage /> },
              { path: "changes/:cid", element: <ChangeDetailPage /> },
              { path: "graph", element: <GraphPage /> },
              { path: "exceptions", element: <ExceptionsPage /> },
              { path: "approvals", element: <ApprovalsPage /> },
              { path: "audit", element: <AuditPage /> },
            ],
          },
        ],
      },
    ],
  },
  { path: "*", element: <Navigate to="/" replace /> },
];
