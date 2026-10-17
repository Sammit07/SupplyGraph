import { Suspense } from "react";

import { RisksView } from "./risks-view";

export const metadata = { title: "Risk register" };

export default function RisksPage() {
  return (
    <Suspense>
      <RisksView />
    </Suspense>
  );
}
