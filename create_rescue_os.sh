#!/bin/bash

# This scripts is intended to regularly create a bootalbe backup of the
# operating system.
#
# Even a USB-key is suitable to backup to. This is probably the cheapest
# way to enable a relatively fast, manual failover without running RAID
# configurations.
#
# !!! The backup must reside on a separate partition !!!
#
# Prior initial execution, make sure to create the separate partition
# of sufficient size (with i.e. fdisk) with preferably the same file
# system as you use for your root partition.
#
# After initial execution, check if you can boot the backup.
#
# usage: ./create_rescue_os.sh <target_UUID>
#
# Put a call to this script in your crontab to create backups regularly,
# (But remember: USB-keys die earlier the more often you write to them.)

#
# functions
#
function error {
	args="${*:1}"
	echo "ERROR:" ${args[@]}
	exit 1
}

function varcheck { # name
	VALUE=$(eval "echo \$${1}")
	echo "${1}=${VALUE}"
	if [ "${VALUE}" = "" ]
	then
	        error "variable empty!"
	fi
}

function bincheck { # name
	BIN=$(eval "whereis -b ${1} | cut -d' ' -f2")
	if [ ! -x $BIN ]
	then
	        error "dependency '${1}' not found!"
	fi
	echo -n $BIN
}

#
# check dependencies
#
RSYNC=$(bincheck rsync)
MOUNT=$(bincheck mount)
UMOUNT=$(bincheck umount)
UPDATE_GRUB=$(bincheck update-grub)
GRUB_INSTALL=$(bincheck grub-install)

#
# collect information
#
if [ "$1" = "" ]
then
	error 	"Argument missing! " \
			"Please provide the UUID of the target partition" \
			"as first argument."
fi

TARGET_UUID="$1"
varcheck TARGET_UUID

MOUNTPOINT=$( grep -v "^#" /etc/fstab | grep $TARGET_UUID | awk '{print $2}')
varcheck MOUNTPOINT

DEVICE=$(blkid | grep $TARGET_UUID | cut -d: -f1 | sed 's/[0-9]*$//g')
varcheck DEVICE

$MOUNT $MOUNTPOINT

#
# check if target is mounted
#
if [ $(df "/" "${MOUNTPOINT}" | sort -u | wc -l) -ne 3 ]
then
	error "target seems not to be mounted!"
fi

#
# check if we must update grub after copying data
#
if [ "$(ls /boot | md5sum)" = "$(ls $MOUNTPOINT/boot | md5sum)" ]
then
	UPDATE_BOOTLOADER=false
else
	UPDATE_BOOTLOADER=true
fi

#
# copy data
#
$RSYNC \
	--archive \
	--verbose \
	--delete-during \
	--rsync-path='nice -n19 rsync' \
	--one-file-system \
	--exclude=/proc/* \
	--exclude=/dev/* \
	--exclude=/tmp/* \
	--exclude=/sys/* \
	--exclude=/run/* \
	--exclude=/home/* \
	--exclude=/var/cache/* \
	--exclude=/var/lock/* \
	--exclude=/var/log/* \
	--exclude=/var/mail/* \
	--exclude=/var/spool/* \
		/ /boot "${MOUNTPOINT}"

#
# update grub
#
if [ $UPDATE_BOOTLOADER = true ]
then
	BINDS="dev proc sys tmp"
	CHROOT="chroot ${MOUNTPOINT}"

	# set up chroot
	for BIND in $BINDS
	do
		$MOUNT --bind /$BIND $MOUNTPOINT/$BIND
	done

	# actually update grub
	$CHROOT $UPDATE_GRUB
	$CHROOT $GRUB_INSTALL $DEVICE

	# tear down chroot
	for BIND in $BINDS
	do
		$UMOUNT -l $MOUNTPOINT/$BIND
	done
fi

echo "unmounting…"

$UMOUNT -l $MOUNTPOINT
