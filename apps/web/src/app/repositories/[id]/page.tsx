import type { Metadata } from "next";
import { Suspense } from "react";
import { Skeleton } from "@/components/ui/primitives";
import { RepositoryDetailView } from "@/components/views/repositories";

export const metadata: Metadata = { title: "Repository" };

export default function Page({ params }: PageProps<"/repositories/[id]">) {
  return (
    <Suspense fallback={<Skeleton className="h-[480px] w-full" />}>
      {params.then(({ id }) => <RepositoryDetailView id={id} />)}
    </Suspense>
  );
}
