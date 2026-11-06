// App: providers and the router. The route table lives in src/routes.tsx.
import { Suspense } from "react";
import { createBrowserRouter, Navigate, RouterProvider, type RouteObject } from "react-router-dom";
import { homeFor } from "./app/navigation";
import { SessionProvider, useSession } from "./app/session";
import { Shell } from "./app/Shell";
import { ThemeProvider } from "./app/theme";
import { AssistantProvider } from "./app/assistant/AssistantContext";
import { SkeletonLines, ToastProvider } from "./components";
import { CASE_LAYOUT, CASE_TABS, NOT_FOUND, ROUTES } from "./routes";

function Home() {
  const { view } = useSession();
  return <Navigate to={homeFor(view)} replace />;
}

function page(Page: React.ComponentType) {
  return (
    <Suspense fallback={<SkeletonLines lines={6} />}>
      <Page />
    </Suspense>
  );
}

const CaseLayout = CASE_LAYOUT;

const routes: RouteObject[] = [
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      ...ROUTES.map((r) => ({ path: r.path.slice(1), element: page(r.page) })),
      {
        path: "cases/:caseId",
        element: page(CaseLayout),
        children: CASE_TABS.map((t) => (t.index ? { index: true, element: page(t.page) } : { path: t.path, element: page(t.page) })),
      },
      { path: "*", element: page(NOT_FOUND) },
    ],
  },
];

const router = createBrowserRouter(routes);

export function App() {
  return (
    <ThemeProvider>
      <ToastProvider>
        <SessionProvider>
          <AssistantProvider>
            <RouterProvider router={router} />
          </AssistantProvider>
        </SessionProvider>
      </ToastProvider>
    </ThemeProvider>
  );
}
