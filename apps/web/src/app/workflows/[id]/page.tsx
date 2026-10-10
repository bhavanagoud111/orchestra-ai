import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui/primitives";
import { ExecutionConsole } from "@/components/views/execution-console";

export const metadata: Metadata = { title: "Execution console" };

export default function Page({ params }: PageProps<"/workflows/[id]">) {
  return (
    <Suspense fallback={<Skeleton className="h-[480px] w-full" />}>
      {params.then(({ id }) => <ExecutionConsole id={id} />)}
    </Suspense>
  );
}
