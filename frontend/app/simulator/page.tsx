import { Suspense } from "react";

import { SimulatorView } from "./simulator-view";

export const metadata = { title: "Impact simulator" };

export default function SimulatorPage() {
  return (
    <Suspense>
      <SimulatorView />
    </Suspense>
  );
}
