#!/usr/bin/perl
# Publish a repository without its private parts, leaving local history untouched.
#
#   perl rewrite-identity-for-push.pl OUTDIR BASE TIP...
#
# For the commits reachable from TIP... (excluding BASE, e.g. the upstream commit you forked from) it
# writes NEW commit and tree objects in which
#   - author/committer lines with the email REWRITE_OLD_EMAIL carry REWRITE_NEW_IDENT instead
#     (e.g. "Name <12345+name@users.noreply.github.com>"),
#   - the folders in REWRITE_DROP_DIRS (comma-separated repo paths) are gone,
#   - with REWRITE_DROP_MEDIA=1, every image and video file is gone.
# It prints "<tip-name> <old-sha> <new-sha>" per tip and never moves a ref: push the new sha yourself
# (git push <remote> <new-sha>:refs/heads/<branch>). History the filters do not touch comes out with
# unchanged hashes, and the rewrite is deterministic, so later pushes line up with earlier ones.
# OUTDIR is scratch space for the object files.
use strict;
use warnings;
use IPC::Open2;
use Digest::SHA qw(sha1_hex);
use File::Path qw(make_path);

my ($outdir, $base, @tips) = @ARGV;
die "usage: rewrite-identity-for-push.pl OUTDIR BASE TIP...\n" unless $outdir && $base && @tips;
make_path($outdir);

my $OLD = $ENV{REWRITE_OLD_EMAIL} // '';
my $NEW = $ENV{REWRITE_NEW_IDENT} // '';
die "set REWRITE_OLD_EMAIL and REWRITE_NEW_IDENT together\n" if ($OLD eq '') != ($NEW eq '');
my $DROP_MEDIA = $ENV{REWRITE_DROP_MEDIA} ? 1 : 0;

open(my $rl, '-|', 'git', 'rev-list', '--topo-order', '--reverse', '--parents', @tips, '--not', $base)
    or die "rev-list: $!";
my @order;
while (my $line = <$rl>) { chomp $line; push @order, [split / /, $line]; }
close $rl or die "rev-list failed";

my $pid = open2(my $out, my $in, 'git', 'cat-file', '--batch');
binmode $out; binmode $in;

my %DROP_DIR = map { $_ => 1 } grep { length } split /\s*,\s*/, ($ENV{REWRITE_DROP_DIRS} // '');
my $MEDIA = qr/\.(?:png|jpe?g|gif|bmp|webp|tga|tiff?|ico|svg|apng|mp4|m4v|mkv|webm|mov|avi|wmv|flv)\z/i;

sub read_obj {
    my ($want) = @_;
    print $in "$want\n"; $in->flush;
    my $hdr = <$out>;
    my ($sha, $type, $size) = split / /, $hdr;
    die "missing object $want: $hdr" if !defined $size;
    my $body = '';
    read($out, $body, $size) == $size or die "short read $want";
    my $nl; read($out, $nl, 1);
    return ($type, $body);
}

# Tree at $prefix without the dropped folders and media files ('' if nothing is left);
# memoised per tree and path, so each distinct tree is read once.
my %tcache;
my @trees_written;
sub filter_tree {
    my ($tree, $prefix) = @_;
    my $key = "$tree $prefix";
    return $tcache{$key} if exists $tcache{$key};
    my ($type, $body) = read_obj($tree);
    die "not a tree: $tree" unless $type eq 'tree';
    my @ents;
    my $pos = 0;
    my $changed = 0;
    while ($pos < length $body) {
        my $nul = index($body, "\0", $pos);
        my ($mode, $name) = split / /, substr($body, $pos, $nul - $pos), 2;
        my $sha = unpack('H*', substr($body, $nul + 1, 20));
        $pos = $nul + 21;
        my $path = $prefix eq '' ? $name : "$prefix/$name";
        if ($mode eq '40000') {
            if ($DROP_DIR{$path}) { $changed = 1; next; }
            my $sub = filter_tree($sha, $path);
            if ($sub eq '') { $changed = 1; next; }
            if ($sub ne $sha) { $changed = 1; $sha = $sub; }
        } elsif ($DROP_MEDIA && $mode ne '160000' && $name =~ $MEDIA) {
            $changed = 1; next;
        }
        push @ents, [$mode, $name, $sha];
    }
    my $result = $tree;
    if ($changed) {
        if (!@ents) {
            $result = '';
        } else {
            my $nb = join('', map { "$_->[0] $_->[1]\0" . pack('H*', $_->[2]) } @ents);
            $result = sha1_hex("tree " . length($nb) . "\0" . $nb);
            my $p = "$outdir/$result.tree";
            open(my $fh, '>:raw', $p) or die "$p: $!";
            print $fh $nb; close $fh;
            push @trees_written, [$p, $result];
        }
    }
    $tcache{$key} = $result;
    return $result;
}

my %map;
my @written;
my $changed = 0;
for my $row (@order) {
    my ($c) = @$row;
    print $in "$c\n"; $in->flush;
    my $hdr = <$out>;
    my ($sha, $type, $size) = split / /, $hdr;
    die "not a commit: $hdr" unless $type eq 'commit';
    my $body = '';
    read($out, $body, $size) == $size or die "short read $c";
    my $nl; read($out, $nl, 1);

    my ($head, $msg) = split /\n\n/, $body, 2;
    my @lines = split /\n/, $head, -1;
    for (@lines) {
        if (/^tree ([0-9a-f]{40})$/) {
            my $t = filter_tree($1, '');
            die "commit $c would have an empty tree\n" if $t eq '';
            $_ = "tree $t";
        } elsif (/^parent ([0-9a-f]{40})$/) {
            my $p = $1;
            $_ = 'parent ' . ($map{$p} // $p);
        } elsif ($OLD ne '' && /^(author|committer) [^<]*<\Q$OLD\E>( .*)$/) {
            $_ = "$1 $NEW$2";
        }
    }
    my $newbody = join("\n", @lines) . "\n\n" . (defined $msg ? $msg : '');
    $newbody = join("\n", @lines) unless defined $msg;
    if ($newbody eq $body) { $map{$c} = $c; next; }
    my $obj = "commit " . length($newbody) . "\0" . $newbody;
    my $new = sha1_hex($obj);
    $map{$c} = $new;
    my $path = "$outdir/$new.commit";
    open(my $fh, '>:raw', $path) or die "$path: $!";
    print $fh $newbody; close $fh;
    push @written, [$path, $new];
    $changed++;
}
close $in; waitpid($pid, 0);

if (@trees_written) {
    my $list = "$outdir/tree-paths.txt";
    open(my $lf, '>', $list) or die; print $lf "$_->[0]\n" for @trees_written; close $lf;
    my @got = `git hash-object -t tree -w --stdin-paths < "$list"`;
    chomp @got;
    for my $i (0..$#trees_written) {
        die "tree hash mismatch $trees_written[$i][1] vs $got[$i]\n" unless $got[$i] eq $trees_written[$i][1];
    }
}
if (@written) {
    my $list = "$outdir/paths.txt";
    open(my $lf, '>', $list) or die; print $lf "$_->[0]\n" for @written; close $lf;
    my @got = `git hash-object -t commit -w --stdin-paths < "$list"`;
    chomp @got;
    for my $i (0..$#written) {
        die "hash mismatch $written[$i][1] vs $got[$i]\n" unless $got[$i] eq $written[$i][1];
    }
}
print STDERR scalar(@order) . " commits walked, $changed rewritten, " . scalar(@trees_written) . " trees rewritten\n";
for my $t (@tips) {
    my $old = `git rev-parse $t`; chomp $old;
    print "$t $old " . ($map{$old} // $old) . "\n";
}
