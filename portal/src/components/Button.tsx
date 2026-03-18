import { ButtonHTMLAttributes, forwardRef } from "react";

type Variant = "default" | "outline" | "ghost" | "destructive";

const base =
  "inline-flex items-center justify-center gap-2 rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 px-4 py-2 cursor-pointer";

const variants: Record<Variant, string> = {
  default: "bg-primary text-primary-foreground hover:bg-primary/90",
  outline:
    "border border-border bg-transparent hover:bg-secondary text-foreground",
  ghost: "hover:bg-secondary text-foreground",
  destructive:
    "bg-destructive text-destructive-foreground hover:bg-destructive/90",
};

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className = "", variant = "default", ...props }, ref) => {
    return (
      <button
        ref={ref}
        className={`${base} ${variants[variant]} ${className}`}
        {...props}
      />
    );
  },
);

Button.displayName = "Button";
