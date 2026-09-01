import { signOut } from "@/features/auth/actions";
import type { User } from "@/features/auth/types";

export function UserMenu({ user }: { user: User }) {
  return (
    <div className="flex items-center gap-3">
      {user.avatar_url && (
        // Avatars come from an arbitrary GitHub CDN path; configuring
        // next/image remote patterns is not worth it for a 24px image.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={user.avatar_url}
          alt=""
          width={24}
          height={24}
          className="rounded-full"
        />
      )}
      <span className="text-sm">{user.login}</span>
      <form action={signOut}>
        <button
          type="submit"
          className="rounded-md border border-black/15 px-3 py-1.5 text-sm transition-colors hover:bg-black/5 dark:border-white/20 dark:hover:bg-white/10"
        >
          Sign out
        </button>
      </form>
    </div>
  );
}
