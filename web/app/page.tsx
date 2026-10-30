"use client";

import dynamic from "next/dynamic";

// Client-only: the grid depends on the browser's time zone and clock, which the
// server can't know, so server rendering it would always mismatch.
const CalendarApp = dynamic(() => import("./components/CalendarApp"), { ssr: false });

export default function Page() {
  return <CalendarApp />;
}
