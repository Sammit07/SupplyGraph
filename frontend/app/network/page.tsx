import { Suspense } from "react";

import { NetworkView } from "./network-view";

export const metadata = { title: "Network" };

export default function NetworkPage() {
  return (
    <Suspense>
      <NetworkView />
    </Suspense>
  );
}
