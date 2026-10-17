import { Suspense } from "react";

import { ExposureView } from "./exposure-view";

export const metadata = { title: "Exposure" };

export default function ExposurePage() {
  return (
    <Suspense>
      <ExposureView />
    </Suspense>
  );
}
