import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { X } from "lucide-react";
import * as React from "react";
import { cn } from "../lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-clay text-white hover:bg-[#7d2f14]",
        quiet: "border border-line bg-card hover:bg-[#f3eadc]",
        pine: "bg-pine text-[#f6f1e6] hover:bg-[#1a2620]",
        danger: "bg-[#7f1d1d] text-white hover:bg-[#641616]",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant }), className)} ref={ref} {...props} />;
  },
);
Button.displayName = "Button";

export function Input(props: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      {...props}
      className={cn(
        "w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none ring-clay/30 focus:ring-2",
        props.className,
      )}
    />
  );
}

export function Select(props: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      {...props}
      className={cn(
        "w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none ring-clay/30 focus:ring-2",
        props.className,
      )}
    />
  );
}

export function Textarea(props: React.TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return (
    <textarea
      {...props}
      className={cn(
        "w-full rounded-md border border-line bg-white px-3 py-2 text-sm outline-none ring-clay/30 focus:ring-2",
        props.className,
      )}
    />
  );
}

export function Label(props: React.LabelHTMLAttributes<HTMLLabelElement>) {
  return <label {...props} className={cn("mb-1 block text-xs font-medium uppercase tracking-wide text-ink/70", props.className)} />;
}

export function Card(props: React.HTMLAttributes<HTMLDivElement>) {
  return <div {...props} className={cn("rounded-xl border border-line bg-card shadow-[0_1px_0_rgba(28,25,21,0.04)]", props.className)} />;
}

export function Badge({ tone = "ink", children }: { tone?: "ink" | "moss" | "amber" | "clay"; children: React.ReactNode }) {
  const tones = {
    ink: "bg-ink/10 text-ink",
    moss: "bg-moss/15 text-moss",
    amber: "bg-amber/15 text-amber",
    clay: "bg-clay/15 text-clay",
  };
  return <span className={cn("inline-flex rounded-full px-2 py-0.5 text-xs font-medium", tones[tone])}>{children}</span>;
}

export function Dialog({
  open,
  onOpenChange,
  title,
  children,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <DialogPrimitive.Root open={open} onOpenChange={onOpenChange}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Overlay className="fixed inset-0 z-40 bg-ink/40" />
        <DialogPrimitive.Content className="fixed left-1/2 top-1/2 z-50 max-h-[90vh] w-[min(36rem,calc(100%-1.5rem))] -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border border-line bg-paper p-5 shadow-xl">
          <div className="mb-4 flex items-start justify-between gap-4">
            <DialogPrimitive.Title className="font-serif text-2xl">{title}</DialogPrimitive.Title>
            <DialogPrimitive.Close className="rounded-md p-1 hover:bg-ink/5" aria-label="Close">
              <X size={16} />
            </DialogPrimitive.Close>
          </div>
          {children}
        </DialogPrimitive.Content>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  );
}

export function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Label>{label}</Label>
      {children}
    </div>
  );
}

export function ErrorNote({ children }: { children: React.ReactNode }) {
  if (!children) return null;
  return <p className="rounded-md bg-clay/10 px-3 py-2 text-sm text-clay">{children}</p>;
}

export function Empty({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-xl border border-dashed border-line bg-card/60 px-4 py-8 text-center">
      <p className="font-serif text-xl">{title}</p>
      <p className="mx-auto mt-1 max-w-md text-sm text-ink/70">{body}</p>
    </div>
  );
}
