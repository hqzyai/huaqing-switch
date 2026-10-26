import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-control text-strong font-medium transition-colors duration-[120ms] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        // 旧变体都映射到 v7 的样式（不再有蓝色 / 绿色按钮）
        default:
          "border border-transparent bg-action font-medium text-action-fg hover:bg-action-hover active:bg-[var(--hq-primary-active)]",
        destructive:
          "border border-transparent bg-destructive text-destructive-foreground hover:bg-[var(--hq-destructive-hover)] active:bg-[var(--hq-destructive-active)]",
        outline:
          "border border-border-strong bg-surface text-fg-1 hover:bg-[var(--hq-outline-hover)] active:bg-[var(--hq-outline-active)]",
        secondary:
          "text-fg-2 hover:bg-[var(--hq-outline-hover)] active:bg-[var(--hq-outline-active)] hover:text-fg-1",
        ghost:
          "text-fg-2 hover:bg-[var(--hq-outline-hover)] active:bg-[var(--hq-outline-active)] hover:text-fg-1",
        mcp: "border border-transparent bg-action font-medium text-action-fg hover:bg-action-hover active:bg-[var(--hq-primary-active)]",
        link: "text-action-text underline-offset-4 hover:underline",
        // ── v7（tokens.css 的 btn-*）：一个视图只有一个实心按钮，其余用 neutral ──
        // 中性实心：页头主操作（操作色，以后的主题色只改这一组变量）
        // 主题色白字对比度偏低，统一 600 字重
        solid:
          "border border-transparent bg-action font-medium text-action-fg hover:bg-action-hover active:bg-[var(--hq-primary-active)]",
        // 中性描边：卡片上的「切换」「添加」「设为默认」等
        neutral:
          "border border-border-strong bg-surface text-fg-1 hover:bg-[var(--hq-outline-hover)] active:bg-[var(--hq-outline-active)] disabled:opacity-50",
        // 透明：页头里带 ⌄ / › 的次要操作
        quiet:
          "border border-transparent text-fg-1 hover:bg-[var(--hq-outline-hover)] active:bg-[var(--hq-outline-active)]",
        // 模式色实心：只用于模式切换的主操作
        direct:
          "border border-transparent bg-direct-solid text-direct-on font-semibold hover:brightness-110",
        route:
          "border border-transparent bg-route-solid text-route-on font-semibold hover:brightness-110",
        stack:
          "border border-transparent bg-stack-solid text-stack-on font-semibold hover:brightness-110",
      },
      size: {
        // shadcn-neutral-v3: standard controls are at least 36px, compact 32px.
        // Keep the existing size prop names and allow text to grow.
        default: "min-h-9 px-3 py-2",
        sm: "min-h-8 px-2 py-1.5",
        lg: "min-h-9 px-3 py-2",
        icon: "min-h-9 min-w-9 p-0",
        // v7：高 28（卡片、对话框里）/ 高 32（页头、模式切换）/ 28 见方的图标按钮
        // 按下缩一下只给「执行动作」的按钮；打开菜单 / 弹层的触发器（aria-haspopup）不缩，弹层自己有进场动画
        compact: "min-h-8 rounded-control px-2 py-1.5 text-strong font-medium",
        regular: "min-h-9 rounded-control px-3 py-2 text-strong font-medium",
        "icon-compact":
          "min-h-8 min-w-8 rounded-control p-0 text-fg-2 hover:text-fg-1",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
